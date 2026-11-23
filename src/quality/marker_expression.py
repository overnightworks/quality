"""Pytest's marker expression (`-m`) as a pure reader: `and`, `or`, `not`, parentheses, names and a name's keyword
arguments, read as pytest's own grammar reads them. The reader is declared here, not taken from pytest, because pytest is
no runtime dependency of quality; a test holds it against the parser installed with quality's development dependencies.
A name with arguments is a name of its own, which no marker set of plain names makes true."""

import itertools
import keyword
import re
from collections.abc import Collection
from dataclasses import dataclass

NAME = re.compile(r"(:?\w|:|\+|-|\.|\[|\]|\\|/)+")
SYMBOLS = frozenset("(),=")
QUOTES = "'\""
WORDS = frozenset({"and", "or", "not"})
BUILTIN_VALUES = frozenset({"True", "False", "None"})
INTEGER = re.compile(r"-?\d+", re.ASCII)


class MarkerExpressionError(ValueError):
    """The text is no expression of pytest's grammar."""


@dataclass(frozen=True)
class Name:
    """A marker name, with its keyword arguments as written when it has any."""

    text: str

    def holds(self, marked: frozenset[str]) -> bool:
        return self.text in marked

    def names(self) -> frozenset[str]:
        return frozenset({self.text})


@dataclass(frozen=True)
class Not:
    operand: Node

    def holds(self, marked: frozenset[str]) -> bool:
        return not self.operand.holds(marked)

    def names(self) -> frozenset[str]:
        return self.operand.names()


@dataclass(frozen=True)
class And:
    operands: tuple[Node, ...]

    def holds(self, marked: frozenset[str]) -> bool:
        return all(operand.holds(marked) for operand in self.operands)

    def names(self) -> frozenset[str]:
        return frozenset().union(*(operand.names() for operand in self.operands))


@dataclass(frozen=True)
class Or:
    operands: tuple[Node, ...]

    def holds(self, marked: frozenset[str]) -> bool:
        return any(operand.holds(marked) for operand in self.operands)

    def names(self) -> frozenset[str]:
        return frozenset().union(*(operand.names() for operand in self.operands))


@dataclass(frozen=True)
class Nothing:
    """The empty expression, which selects no test."""

    def holds(self, marked: frozenset[str]) -> bool:
        return False

    def names(self) -> frozenset[str]:
        return frozenset()


type Node = Name | Not | And | Or | Nothing


@dataclass(frozen=True)
class Expression:
    root: Node

    def holds(self, marked: Collection[str]) -> bool:
        """Whether a test carrying exactly these markers is selected."""
        return self.root.holds(frozenset(marked))

    def can_select(self, marker: str) -> bool:
        """Whether a test carrying `marker` is selected for some set of other markers."""
        others = sorted(self.root.names() - {marker})
        for size in range(len(others) + 1):
            for chosen in itertools.combinations(others, size):
                if self.root.holds(frozenset({marker, *chosen})):
                    return True
        return False


def parse(text: str) -> Expression:
    """The expression `text` is, or a `MarkerExpressionError` when pytest's grammar refuses it."""
    try:
        return Expression(_Reader(_tokens(text)).expression())
    except RecursionError as error:
        raise MarkerExpressionError("nested too deeply") from error


def _tokens(text: str) -> list[str]:
    found = []
    position = 0
    while position < len(text):
        character = text[position]
        if character in " \t":
            position += 1
            continue
        if character in SYMBOLS:
            word = character
        elif character in QUOTES:
            end = text.find(character, position + 1)
            word = text[position : end + 1] if end != -1 else ""
            if not word or "\\" in word:
                raise MarkerExpressionError(f"bad string at {position}")
        else:
            matched = NAME.match(text, position)
            if matched is None:
                raise MarkerExpressionError(f'unexpected character "{character}"')
            word = matched.group(0)
        found.append(word)
        position += len(word)
    return found


class _Reader:
    """Recursive descent over the tokens: expression, `or`, `and`, `not`, a name with its arguments."""

    def __init__(self, tokens: list[str]) -> None:
        self.tokens = tokens
        self.position = 0

    def expression(self) -> Node:
        if not self.tokens:
            return Nothing()
        node = self._disjunction()
        if self._peek() is not None:
            raise MarkerExpressionError(f"unexpected {self._peek()}")
        return node

    def _peek(self) -> str | None:
        return self.tokens[self.position] if self.position < len(self.tokens) else None

    def _take(self, word: str) -> bool:
        if self._peek() == word:
            self.position += 1
            return True
        return False

    def _next(self) -> str:
        word = self._peek()
        if word is None:
            raise MarkerExpressionError("expression ends early")
        self.position += 1
        return word

    def _disjunction(self) -> Node:
        operands = [self._conjunction()]
        while self._take("or"):
            operands.append(self._conjunction())
        return operands[0] if len(operands) == 1 else Or(tuple(operands))

    def _conjunction(self) -> Node:
        operands = [self._negation()]
        while self._take("and"):
            operands.append(self._negation())
        return operands[0] if len(operands) == 1 else And(tuple(operands))

    def _negation(self) -> Node:
        if self._take("not"):
            return Not(self._negation())
        if self._take("("):
            node = self._disjunction()
            self._require(")")
            return node
        name = self._name()
        if not self._take("("):
            return Name(name)
        arguments = [self._argument()]
        while self._take(","):
            arguments.append(self._argument())
        self._require(")")
        return Name(f"{name}({', '.join(arguments)})")

    def _name(self) -> str:
        word = self._next()
        if word in WORDS or word in SYMBOLS or word[0] in QUOTES:
            raise MarkerExpressionError(f"expected a name, found {word}")
        return word

    def _require(self, word: str) -> None:
        if not self._take(word):
            raise MarkerExpressionError(f"expected {word}")

    def _argument(self) -> str:
        name = self._name()
        if not name.isidentifier() or keyword.iskeyword(name):
            raise MarkerExpressionError(f"{name} is no argument name")
        self._require("=")
        value = self._next()
        if value[0] not in QUOTES and not (INTEGER.fullmatch(value) or value in BUILTIN_VALUES):
            raise MarkerExpressionError(f"{value} is no argument value")
        return f"{name}={value}"
