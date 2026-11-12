"""The shell reader of the workflow rules: a `run:` script read as POSIX shell words, never run.

A script becomes logical lines (joined at a trailing backslash, comments dropped), each line becomes segments of words
(split at `&&`, `||`, `;`, `|`, `&`, `(` and `)`, backticks read as `$(…)`), each segment's command word is found past
assignments, prefix words and the wrappers `env`, `sudo`, `timeout`, `nice` and `xargs` with their flags, and a shell's
`-c` string is split into segments of its own. Each segment keeps the separator that opened it, the arguments of a call of
a named tool are read behind its interpreter or runner, and the loop over a line's segments takes the rule's judge. The
module reads text only and imports no other module of quality."""

import re
import shlex
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, replace
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
LONE_DOLLAR = ("$",)
DYNAMIC_COMMAND = re.compile(r"\$[\w{]")
C_STRING_OPENER = "-c"
PIPES = frozenset({"|", "|&"})
PYTHON_INTERPRETER = re.compile(r"python(?:3(?:\.\d+t?)?)?")
PYPY_INTERPRETER = re.compile(r"pypy(?:3(?:\.\d+)?)?")
INTERPRETERS = (PYTHON_INTERPRETER, PYPY_INTERPRETER)
INTERPRETER_VALUE_FLAGS = frozenset({"-W", "-X", "--check-hash-based-pycs"})
RUNNERS = {
    ("uv", "run"): False,
    ("poetry", "run"): False,
    ("pipenv", "run"): False,
    ("pdm", "run"): False,
    ("hatch", "run"): False,
    ("coverage", "run"): False,
    ("uvx",): True,
    ("npx",): True,
    ("bunx",): True,
    ("npm", "exec"): True,
    ("pnpm", "exec"): True,
    ("pnpm", "dlx"): True,
    ("yarn",): True,
}


@dataclass(frozen=True)
class Segment:
    """The words of one command and the separator word that opened it: `|`, `|&`, `&&`, `||`, `;`, `&`, `(`, `-c` for the
    segment a shell's `-c` string opens, empty at the start of a line."""

    words: tuple[str, ...]
    opened_by: str


@dataclass(frozen=True)
class Tool:
    """A tool a rule looks for: its command words, and the module a Python interpreter runs with `-m`."""

    commands: frozenset[str]
    module: str


@dataclass
class _Open:
    """A segment while the lexer fills it."""

    words: list[str]
    opened_by: str


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


def segments(text: str) -> list[Segment]:
    """The words of a line, split at `&&`, `||`, `;`, `|`, `&`, `(` and `)`, a backtick substitution read as `$(…)`; a word that holds a substitution is split again, except the string of a shell's `-c`, which is read as segments of its own after the shell's."""
    result: list[Segment] = []
    for part in _lex(_backticks_as_dollar_substitutions(text), False, _Open([], ""))[0]:
        if part.words:
            segment = Segment(tuple(part.words), part.opened_by)
            result.append(segment)
            result.extend(_shell_script_segments(segment.words))
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


def _lex(text: str, after_substitution: bool, leading: _Open) -> tuple[list[_Open], bool]:
    """The segments of `text` after the words of the `leading` segment, and whether it ends on the separator that closes a substitution that stands in a command with a command word; a lone `$` right after one opens the next substitution of the same command and is dropped."""
    lexer = shlex.shlex(text, posix=True, punctuation_chars="".join(sorted(LEXER_PUNCTUATION)))
    lexer.whitespace_split = True
    lexer.commenters = ""
    parts: list[_Open] = [_Open(list(leading.words), leading.opened_by)]
    openers: list[int] = []
    for word in lexer:
        if word and set(word) <= SEPARATOR_CHARACTERS:
            after_substitution = _read_separator(word, parts, openers)
            parts.append(_Open([], word))
        elif word == "$" and after_substitution and not parts[-1].words:
            after_substitution = False
        elif SUBSTITUTION.search(word) and not _is_c_string(parts[-1].words, word):
            inner, after_substitution = _lex(word, after_substitution, parts[-1])
            parts[-1:] = inner
        else:
            parts[-1].words.append(word)
            after_substitution = False
    return parts, after_substitution


def _read_separator(word: str, parts: list[_Open], openers: list[int]) -> bool:
    """Whether the separator `word` closes a substitution whose opening segment holds a command word; the indexes of the segments that opened a substitution are kept in `openers`."""
    closes_with_command = False
    for character in word:
        if character == "(":
            openers.append(len(parts) - 1)
        elif character == ")" and openers:
            opener = parts[openers.pop()].words
            closes_with_command = command_start(opener) < len(opener)
    return closes_with_command


def with_env_strings_split(words: Sequence[str]) -> list[str]:
    """The words with the value of each `env -S` replaced by the words it splits into, placed where the value stood."""
    result: list[str] = []
    index = 0
    while index < len(words):
        result.append(words[index])
        index += 1
        if words[index - 1] == "env":
            index = _copy_env_flags(words, index, result)
    return result


def _copy_env_flags(words: Sequence[str], index: int, result: list[str]) -> int:
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


def command_start(words: Sequence[str]) -> int:
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


def _after_wrapper(words: Sequence[str], index: int) -> int:
    """The index past the wrapper at `index`, its flags and the operands it takes before its command word."""
    after_flags = _after_flags(words, index + 1, WRAPPER_VALUE_FLAGS[words[index]])
    return min(after_flags + WRAPPER_OPERANDS.get(words[index], 0), len(words))


def _after_flags(words: Sequence[str], index: int, value_flags: frozenset[str]) -> int:
    """The index past the flags from `index` on, each flag in `value_flags` taking the word after it as its value."""
    while index < len(words) and words[index].startswith("-"):
        index += 2 if words[index] in value_flags else 1
    return index


def _shell_script_segments(words: Sequence[str]) -> list[Segment]:
    """The segments of the `-c` string of a shell that starts `words`, read as a script of its own and opened by the flag; none when no shell with a `-c` string starts them."""
    index = command_start(words)
    if index >= len(words) or PurePosixPath(words[index]).name not in SHELLS:
        return []
    arguments = words[index + 1 :]
    string_index = _c_string_index(arguments)
    inner = [] if string_index is None else segments(arguments[string_index])
    return [replace(inner[0], opened_by=C_STRING_OPENER), *inner[1:]] if inner else []


def _is_c_string(words: Sequence[str], word: str) -> bool:
    """Whether `word` would be the `-c` string of the shell that the words so far start."""
    index = command_start(words)
    if index >= len(words) or PurePosixPath(words[index]).name not in SHELLS:
        return False
    arguments = [*words[index + 1 :], word]
    return _c_string_index(arguments) == len(arguments) - 1


def _c_string_index(arguments: Sequence[str]) -> int | None:
    """The index of the first operand after a shell's flags, when a `-c` flag came before it; a lone `-` or `--` ends the flags, and the word after it is the operand."""
    has_c_flag = False
    index = 0
    while index < len(arguments) and _is_shell_flag(arguments[index]):
        has_c_flag = has_c_flag or _is_c_flag(arguments[index])
        index += 2 if _shell_flag_takes_value(arguments[index]) else 1
    if index < len(arguments) and arguments[index] in ("-", "--"):
        index += 1
    return index if has_c_flag and index < len(arguments) else None


def _is_shell_flag(word: str) -> bool:
    """A word that starts with a dash or a plus sign, other than a lone `-` or `--`, which end the flags."""
    return word.startswith(("-", "+")) and word not in ("-", "--")


def _is_c_flag(word: str) -> bool:
    """A single-dash word that holds the letter `c`, as `-c`, `-ec` and `-lc` do."""
    return word.startswith("-") and not word.startswith("--") and "c" in word


def _shell_flag_takes_value(word: str) -> bool:
    """`-o`, `+o`, `-O`, `+O` and any single-dash or plus word ending in `o` or `O` take the next word; so do `--rcfile` and `--init-file`."""
    return word in SHELL_LONG_VALUE_FLAGS or (not word.startswith("--") and word.endswith(("o", "O")))


def command_words(segment: Segment) -> tuple[str, ...]:
    """The words of the segment from its command word on."""
    return segment.words[command_start(segment.words) :]


def tool_arguments(words: Sequence[str], tool: Tool) -> tuple[str, ...] | None:
    """The words after the tool's command word when `words` start a call of it, directly, through a Python interpreter's `-m` or behind a runner; None otherwise."""
    if not words:
        return None
    command = PurePosixPath(words[0]).name
    if command in tool.commands:
        return tuple(words[1:])
    if any(interpreter.fullmatch(command) for interpreter in INTERPRETERS):
        return _module_arguments(words[1:], tool)
    for runner in ((command, *words[1:2]), (command,)):
        if runner in RUNNERS:
            return _runner_arguments(words[len(runner) :], tool, RUNNERS[runner])
    return None


def _module_arguments(words: Sequence[str], tool: Tool) -> tuple[str, ...] | None:
    """The words after `-m <module>`, when only flags stand before the `-m`; a flag of `INTERPRETER_VALUE_FLAGS` takes a value."""
    index = 0
    while index < len(words):
        word = words[index]
        if word == "-m":
            return tuple(words[index + 2 :]) if tuple(words[index + 1 : index + 2]) == (tool.module,) else None
        if not word.startswith("-"):
            return None
        index += 2 if word in INTERPRETER_VALUE_FLAGS else 1
    return None


def _runner_arguments(words: Sequence[str], tool: Tool, strips_version: bool) -> tuple[str, ...] | None:
    """The call of the tool a runner starts, past its flags and the values of its flags; a runner that fetches its tool reads the tool word without a trailing `@version`."""
    index = 0
    while index < len(words):
        if (arguments := tool_arguments(_words_from(words, index, strips_version), tool)) is not None:
            return arguments
        if not words[index].startswith("-"):
            return None
        index += 1
        if (
            index < len(words)
            and not words[index].startswith("-")
            and tool_arguments(_words_from(words, index, strips_version), tool) is None
        ):
            index += 1
    return None


def _words_from(words: Sequence[str], index: int, strips_version: bool) -> list[str]:
    """The words from `index` on, the word at `index` without a trailing `@version` when the runner reads it as its tool word."""
    if not strips_version:
        return list(words[index:])
    return [words[index].partition("@")[0], *words[index + 1 :]]


@dataclass(frozen=True)
class LineReading[Problem]:
    """What a rule brings to the loop over a line's segments: the judge of one segment (given all of them and its index), whether a command is a call the rule reads, whether a text holds a word that makes a hidden command a finding, and the two problems the loop itself reports."""

    judge: Callable[[Sequence[Segment], int], Problem | None]
    starts_call: Callable[[Sequence[str]], bool]
    holds_hidden_word: Callable[[str], bool]
    unreadable: Problem
    built_at_run_time: Problem


def line_problems[Problem](text: str, reading: LineReading[Problem], script_holds_hidden_word: bool) -> Iterator[Problem]:
    """The problems of one logical line, read with the value of each `env -S` split into its words: the line the lexer refuses when it holds a hidden word, each segment the judge faults, and a command built at run time when the whole script holds a hidden word."""
    try:
        parts = [Segment(tuple(with_env_strings_split(part.words)), part.opened_by) for part in segments(text)]
    except ValueError:
        if reading.holds_hidden_word(text):
            yield reading.unreadable
        return
    for index, part in enumerate(parts):
        problem = reading.judge(parts, index)
        if problem is not None:
            yield problem
        elif script_holds_hidden_word and _is_built_at_run_time(parts, index, reading.starts_call):
            yield reading.built_at_run_time


def _is_built_at_run_time(parts: Sequence[Segment], index: int, starts_call: Callable[[Sequence[str]], bool]) -> bool:
    """A command word that is a substitution (a lone `$`) whose own command is no call the rule reads, `eval`, or a word that starts with `$`."""
    command = command_words(parts[index])
    if command == LONE_DOLLAR:
        return not (index + 1 < len(parts) and starts_call(command_words(parts[index + 1])))
    return bool(command) and (command[0] == "eval" or bool(DYNAMIC_COMMAND.match(command[0]))) and not starts_call(command)
