"""The shell reader of the workflow rules: a `run:` script read as POSIX shell words, never run.

A script becomes logical lines (joined at a trailing backslash, comments dropped), each line becomes segments of words
(split at `&&`, `||`, `;`, `|`, `&`, `(` and `)`, backticks read as `$(…)`), each segment's command word is found past
assignments, prefix words and the wrappers `env`, `sudo`, `timeout`, `nice` and `xargs` with their flags, and a shell's
`-c` string is split into segments of its own. The module reads text only and imports no other module of quality."""

import re
import shlex
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import PurePosixPath

import yaml

RUN_EXPRESSION = re.compile(r"\$\{\{.*?\}\}")
DYNAMIC_EXPRESSION = "$EXPRESSION"
ASSIGNMENT = re.compile(r"\w+=")
LEXER_PUNCTUATION = frozenset("();<>|&")
SEPARATOR_CHARACTERS = frozenset("();|&")
PREFIX_WORDS = frozenset({"time", "exec", "command", "nohup", "if", "then", "elif", "else", "do", "while", "until", "!", "{"})
ENV_VALUE_FLAGS = frozenset({"-u", "--unset", "-C", "--chdir", "-a", "--argv0"})
ENV_SPLIT_FLAGS = frozenset({"-S", "--split-string"})
ENV_SPLIT_PREFIXES = ("-S", "--split-string=")
SUDO_VALUE_FLAGS = frozenset({"-u", "-g", "-D", "-C", "-h", "-p", "-r", "-t", "-T", "-U", "-R"})
TIMEOUT_VALUE_FLAGS = frozenset({"-k", "-s", "--kill-after", "--signal"})
NICE_VALUE_FLAGS = frozenset({"-n", "--adjustment"})
XARGS_VALUE_FLAGS = frozenset(
    {
        "-I",
        "-n",
        "-P",
        "-d",
        "-E",
        "-L",
        "-s",
        "-a",
        "--replace",
        "--max-args",
        "--max-procs",
        "--delimiter",
        "--eof",
        "--max-lines",
        "--max-chars",
        "--arg-file",
    }
)
WRAPPER_VALUE_FLAGS = {
    "env": ENV_VALUE_FLAGS,
    "sudo": SUDO_VALUE_FLAGS,
    "timeout": TIMEOUT_VALUE_FLAGS,
    "nice": NICE_VALUE_FLAGS,
    "xargs": XARGS_VALUE_FLAGS,
}
WRAPPER_OPERANDS = {"timeout": 1}
SHELLS = frozenset({"sh", "bash", "dash", "zsh", "ksh"})
SHELL_LONG_VALUE_FLAGS = frozenset({"--rcfile", "--init-file"})
SUBSTITUTION = re.compile(r"\$\(")
LONE_DOLLAR = ["$"]


@dataclass(frozen=True)
class ScriptLine:
    """A logical line of a `run:` script without its comments, and the file lines of its first and last physical line."""

    text: str
    first: int
    last: int


def physical_lines(run: yaml.ScalarNode) -> list[tuple[str, int]]:
    """The value line by line, as the shell reads it; a literal block scalar keeps each file line, any other style the first."""
    lines = run.value.split("\n")
    if run.style == "|":
        first = run.start_mark.line + 2
        return [(RUN_EXPRESSION.sub(DYNAMIC_EXPRESSION, line), first + offset) for offset, line in enumerate(lines[: len(lines) - run.value.endswith("\n")])]
    first = run.start_mark.line + (2 if run.style == ">" else 1)
    return [(RUN_EXPRESSION.sub(DYNAMIC_EXPRESSION, line), first) for line in lines]


def logical_lines(physical: list[tuple[str, int]]) -> Iterator[ScriptLine]:
    """Lines joined at a trailing backslash, each without the comment that ends its physical lines."""
    parts: list[str] = []
    first = 0
    for text, number in physical:
        first = first if parts else number
        code = _without_comment(text)
        parts.append(code.removesuffix("\\"))
        if not code.endswith("\\"):
            yield ScriptLine("".join(parts), first, number)
            parts = []
    if parts:
        yield ScriptLine("".join(parts), first, physical[-1][1])


def _without_comment(line: str) -> str:
    """The line up to a `#` that starts a word outside quotes."""
    quote = ""
    escaped = False
    for index, character in enumerate(line):
        if escaped:
            escaped = False
        elif character == "\\" and quote != "'":
            escaped = True
        elif quote:
            quote = "" if character == quote else quote
        elif character in "'\"":
            quote = character
        elif character == "#" and (index == 0 or line[index - 1].isspace() or line[index - 1] in ";&|()"):
            return line[:index]
    return line


def segments(text: str) -> list[list[str]]:
    """The words of a line, split at `&&`, `||`, `;`, `|`, `&`, `(` and `)`, a backtick substitution read as `$(…)`; a word that holds a substitution is split again, and a shell's `-c` string is read as segments after them."""
    result: list[list[str]] = []
    for words in _lex(_backticks_as_dollar_substitutions(text), False, [])[0]:
        if words:
            result.append(words)
            result.extend(_shell_script_segments(words))
    return result


def _backticks_as_dollar_substitutions(text: str) -> str:
    """The text with each pair of backticks that no quote or backslash protects written as `$(` and `)`; a last backtick left without a partner opens a substitution that spans lines when a word follows it and is dropped otherwise."""
    quote = ""
    escaped = False
    backticks: list[int] = []
    for index, character in enumerate(text):
        if escaped:
            escaped = False
        elif character == "\\" and quote != "'":
            escaped = True
        elif character == quote:
            quote = ""
        elif character == "`" and quote != "'":
            backticks.append(index)
        elif not quote and character in "'\"":
            quote = character
    replacements = {position: "$(" if number % 2 == 0 else ")" for number, position in enumerate(backticks)}
    if len(backticks) % 2:
        replacements[backticks[-1]] = "$(" if text[backticks[-1] + 1 :].strip() else " "
    return "".join(replacements.get(index, character) for index, character in enumerate(text))


def _lex(text: str, after_substitution: bool, leading: list[str]) -> tuple[list[list[str]], bool]:
    """The segments of `text` after the `leading` words of its segment, and whether it ends on the separator that closes a substitution that stands in a command with a command word; a lone `$` right after one opens the next substitution of the same command and is dropped."""
    lexer = shlex.shlex(text, posix=True, punctuation_chars="".join(sorted(LEXER_PUNCTUATION)))
    lexer.whitespace_split = True
    lexer.commenters = ""
    segments: list[list[str]] = [list(leading)]
    openers: list[int] = []
    for word in lexer:
        if word and set(word) <= SEPARATOR_CHARACTERS:
            after_substitution = _read_separator(word, segments, openers)
            segments.append([])
        elif word == "$" and after_substitution and not segments[-1]:
            after_substitution = False
        elif SUBSTITUTION.search(word):
            inner, after_substitution = _lex(word, after_substitution, segments[-1])
            segments[-1:] = inner
        else:
            segments[-1].append(word)
            after_substitution = False
    return segments, after_substitution


def _read_separator(word: str, segments: list[list[str]], openers: list[int]) -> bool:
    """Whether the separator `word` closes a substitution whose opening segment holds a command word; the indexes of the segments that opened a substitution are kept in `openers`."""
    closes_with_command = False
    for character in word:
        if character == "(":
            openers.append(len(segments) - 1)
        elif character == ")" and openers:
            opener = segments[openers.pop()]
            closes_with_command = command_start(opener) < len(opener)
    return closes_with_command


def with_env_strings_split(words: list[str]) -> list[str]:
    """The words with the value of each `env -S` replaced by the words it splits into, placed where the value stood."""
    result: list[str] = []
    index = 0
    while index < len(words):
        result.append(words[index])
        index += 1
        if words[index - 1] == "env":
            index = _copy_env_flags(words, index, result)
    return result


def _copy_env_flags(words: list[str], index: int, result: list[str]) -> int:
    """Copies the flags of an `env` from `index` on, splitting the value of each `-S`; returns the index of its first operand."""
    while index < len(words) and words[index].startswith("-"):
        word = words[index]
        index += 1
        if word in ENV_SPLIT_FLAGS:
            if index < len(words):
                result.extend(shlex.split(words[index]))
            index += 1
        elif (glued := _glued_split_value(word)) is not None:
            result.extend(shlex.split(glued))
        else:
            result.append(word)
            if word in ENV_VALUE_FLAGS and index < len(words):
                result.append(words[index])
                index += 1
    return index


def _glued_split_value(word: str) -> str | None:
    """The value of `-S'…'` or `--split-string=…`, None for any other word."""
    for prefix in ENV_SPLIT_PREFIXES:
        if word.startswith(prefix):
            return word[len(prefix) :]
    return None


def command_start(words: list[str]) -> int:
    """The index of the command word, after assignments, the wrappers with their flags and operands and the prefix words."""
    index = 0
    while index < len(words):
        if words[index] in WRAPPER_VALUE_FLAGS:
            index = _after_wrapper(words, index)
        elif words[index] in PREFIX_WORDS or ASSIGNMENT.match(words[index]):
            index += 1
        else:
            break
    return index


def _after_wrapper(words: list[str], index: int) -> int:
    """The index past the wrapper at `index`, its flags and the operands it takes before its command word."""
    after_flags = _after_flags(words, index + 1, WRAPPER_VALUE_FLAGS[words[index]])
    return min(after_flags + WRAPPER_OPERANDS.get(words[index], 0), len(words))


def _after_flags(words: list[str], index: int, value_flags: frozenset[str]) -> int:
    """The index past the flags from `index` on, each flag in `value_flags` taking the word after it as its value."""
    while index < len(words) and words[index].startswith("-"):
        index += 2 if words[index] in value_flags else 1
    return index


def _shell_script_segments(words: list[str]) -> list[list[str]]:
    """The segments of the `-c` string of a shell that starts `words`, read as a script of its own; none when no shell with a `-c` string starts them."""
    index = command_start(words)
    if index >= len(words) or PurePosixPath(words[index]).name not in SHELLS:
        return []
    string = _c_string(words[index + 1 :])
    return [] if string is None else segments(string)


def _c_string(arguments: list[str]) -> str | None:
    """The first operand after a shell's flags, when a `-c` flag came before it; a lone `-` or `--` ends the flags, and the word after it is the operand."""
    has_c_flag = False
    index = 0
    while index < len(arguments) and _is_shell_flag(arguments[index]):
        has_c_flag = has_c_flag or _is_c_flag(arguments[index])
        index += 2 if _shell_flag_takes_value(arguments[index]) else 1
    if index < len(arguments) and arguments[index] in ("-", "--"):
        index += 1
    return arguments[index] if has_c_flag and index < len(arguments) else None


def _is_shell_flag(word: str) -> bool:
    """A word that starts with a dash or a plus sign, other than a lone `-` or `--`, which end the flags."""
    return word.startswith(("-", "+")) and word not in ("-", "--")


def _is_c_flag(word: str) -> bool:
    """A single-dash word that holds the letter `c`, as `-c`, `-ec` and `-lc` do."""
    return word.startswith("-") and not word.startswith("--") and "c" in word


def _shell_flag_takes_value(word: str) -> bool:
    """`-o`, `+o`, `-O`, `+O` and any single-dash or plus word ending in `o` or `O` take the next word; so do `--rcfile` and `--init-file`."""
    return word in SHELL_LONG_VALUE_FLAGS or (not word.startswith("--") and word.endswith(("o", "O")))
