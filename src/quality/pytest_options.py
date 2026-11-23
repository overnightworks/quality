"""A pytest argument vector as pytest reads it: the options before the first `--`, each with its value.

A short option letter that takes a value ends its cluster, and the rest of the word, else the next word, is its value; one
leading `=` of a glued value is dropped. A long option is read by its full name only (`--opt=value` or `--opt value`),
because pytest's parser refuses abbreviations. The letters that take a value and the spellings of the options the rules
read are declared here, not read from pytest at run time: pytest and its plugins are the caller's, not quality's, and a
test holds the tables against the parsers installed with quality's own development dependencies. A `@file` argument and an
option a repository's own plugin adds are not read."""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from quality.run_script import Tool, tool_arguments

PYTEST = Tool(commands=frozenset({"pytest", "py.test"}), module="pytest")
VALUE_LETTERS = frozenset("porkmcWn")
LONG_VALUE_OPTIONS = frozenset({"--numprocesses", "--verbosity", "--randomly-seed"})
WORKERS_NAMES = frozenset({"-n", "--numprocesses"})
LISTING_NAMES = frozenset({"--collect-only", "--collectonly", "--co"})
PLUGIN_NAMES = frozenset({"-p"})
SEPARATOR = "--"


@dataclass(frozen=True)
class Option:
    """One option of a call: its name, its value ("" for a flag), and whether it starts its word, which a `-p` must for pytest to load or switch off a plugin."""

    name: str
    value: str
    starts_word: bool


def pytest_arguments(command: Sequence[str]) -> tuple[str, ...] | None:
    return tool_arguments(command, PYTEST)


def options(arguments: Sequence[str]) -> Iterator[Option]:
    """The options before the first `--`, each short option of a cluster apart, with its value."""
    index = 0
    while index < len(arguments) and arguments[index] != SEPARATOR:
        word = arguments[index]
        index += 1
        if word.startswith(SEPARATOR):
            found, index = _long_option(word, arguments, index)
        elif word.startswith("-"):
            found, index = _short_cluster(word, arguments, index)
        else:
            continue
        yield from found


def _long_option(word: str, arguments: Sequence[str], index: int) -> tuple[list[Option], int]:
    """The option of a `--name[=value]` word and the index past its value."""
    name, equals, glued = word.partition("=")
    value, index = (glued, index) if equals or name not in LONG_VALUE_OPTIONS else _next_word(arguments, index)
    return [Option(name, value, True)], index


def _short_cluster(word: str, arguments: Sequence[str], index: int) -> tuple[list[Option], int]:
    """The options of a `-abc` cluster, ended by the first letter that takes a value, and the index past that value."""
    found = []
    for position, letter in enumerate(word[1:], 1):
        if letter in VALUE_LETTERS:
            glued = word[position + 1 :]
            value, index = (glued.removeprefix("="), index) if glued else _next_word(arguments, index)
            found.append(Option(f"-{letter}", value, position == 1))
            break
        found.append(Option(f"-{letter}", "", position == 1))
    return found, index


def _next_word(arguments: Sequence[str], index: int) -> tuple[str, int]:
    """The word at `index` and the index past it; empty at the end."""
    return (arguments[index], index + 1) if index < len(arguments) else ("", index)


def lists_only(called: Sequence[Option]) -> bool:
    """Whether the call lists tests and runs none."""
    return any(option.name in LISTING_NAMES for option in called)
