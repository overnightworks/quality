"""A lexer for the TypeScript and JavaScript a test file holds: strings, templates, regex literals, identifiers, punctuation and
numbers as typed tokens with the line they start on, comments skipped. It decides only what a reader of locator positions
needs, and it refuses only a construct that does not close, because the tokens after it would be wrong in silence."""

import re
from dataclasses import dataclass
from enum import Enum, auto


class Kind(Enum):
    STRING = auto()
    TEMPLATE = auto()
    REGEX = auto()
    IDENTIFIER = auto()
    PUNCTUATION = auto()
    NUMBER = auto()


@dataclass(frozen=True)
class Token:
    """A template with a `${` hole has the kind TEMPLATE and no value; a template without one is a STRING."""

    kind: Kind
    value: str
    line: int


class ScriptSyntaxError(ValueError):
    """A construct that does not close, with the line it starts on."""

    def __init__(self, reason: str, line: int) -> None:
        super().__init__(f"{reason} at line {line}")


REGEX_KEYWORDS = frozenset(
    {"return", "typeof", "instanceof", "in", "of", "new", "delete", "void", "throw", "case", "do", "else", "yield", "await", "default", "extends"}
)
NON_REGEX_PUNCTUATION = frozenset({")", "]"})
LEXEME = re.compile(
    r"(?P<space>\s+)|(?P<comment>//[^\n]*|/\*.*?\*/)|(?P<unclosed>/\*)|(?P<word>(?:[^\W\d]|\$)[\w$]*)|(?P<number>\d[\w.]*)"
    r"|(?P<quote>['\"])|(?P<template>`)|(?P<slash>/)|(?P<other>.)",
    re.DOTALL,
)
STRINGS = {
    quote: re.compile(rf"{quote}((?:[^{quote}\\\r\n]++|\\(?:\r\n|.))*+){quote}", re.DOTALL) for quote in ("'", '"')
}
TEMPLATE_CHUNK = re.compile(r"(?:[^`\\$]++|\\.|\$(?!\{))*+", re.DOTALL)
REGEX = re.compile(r"/((?:[^/\\\[\r\n]++|\\[^\r\n]|\[(?:[^\]\\\r\n]++|\\[^\r\n])*+\])*+)/\w*")
ESCAPE = re.compile(r"\\(?:u\{([0-9a-fA-F]+)\}|u([0-9a-fA-F]{4})|x([0-9a-fA-F]{2})|(\r\n|.))", re.DOTALL)
SIMPLE_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}
LINE_TERMINATORS = frozenset({"\n", "\r", "\r\n", "\u2028", "\u2029"})
LAST_CODE_POINT = 0x10FFFF


def _unescape_one(escape: re.Match[str]) -> str:
    code = escape.group(1) or escape.group(2) or escape.group(3)
    if code:
        return chr(min(int(code, 16), LAST_CODE_POINT))
    character = escape.group(4)
    if character in LINE_TERMINATORS:
        return ""
    return SIMPLE_ESCAPES.get(character, character)


def _unescape(body: str) -> str:
    return ESCAPE.sub(_unescape_one, body)


class _Lexer:
    def __init__(self, source: str) -> None:
        self.source = source
        self.position = 0
        self.line = 1
        self.tokens: list[Token] = []
        # One entry per open brace: the line of the template whose `${` hole it opens, None for a plain brace.
        self.braces: list[int | None] = []

    def run(self) -> list[Token]:
        handlers = {
            "space": self._skip, "comment": self._skip, "unclosed": self._unclosed_comment, "word": self._word,
            "number": self._number, "quote": self._string, "template": self._template_start, "slash": self._slash,
            "other": self._other,
        }
        while self.position < len(self.source):
            lexeme = self._first(LEXEME)
            handlers[str(lexeme.lastgroup)](lexeme)
        unclosed = [line for line in self.braces if line is not None]
        if unclosed:
            raise ScriptSyntaxError("a template or its ${ hole is not closed", unclosed[0])
        return self.tokens

    def _first(self, pattern: re.Pattern[str]) -> re.Match[str]:
        """The match of a pattern that matches at every position, here: its `other` branch or an empty chunk."""
        return next(pattern.finditer(self.source, self.position))

    def _consume(self, end: int) -> None:
        self.line += self.source.count("\n", self.position, end)
        self.position = end

    def _emit(self, kind: Kind, value: str, end: int) -> None:
        self.tokens.append(Token(kind, value, self.line))
        self._consume(end)

    def _skip(self, lexeme: re.Match[str]) -> None:
        self._consume(lexeme.end())

    def _unclosed_comment(self, _: re.Match[str]) -> None:
        raise ScriptSyntaxError("a block comment is not closed", self.line)

    def _word(self, lexeme: re.Match[str]) -> None:
        self._emit(Kind.IDENTIFIER, lexeme.group(), lexeme.end())

    def _number(self, lexeme: re.Match[str]) -> None:
        self._emit(Kind.NUMBER, lexeme.group(), lexeme.end())

    def _string(self, lexeme: re.Match[str]) -> None:
        string = STRINGS[lexeme.group()].match(self.source, self.position)
        if string is None:
            raise ScriptSyntaxError("a string reaches the end of its line", self.line)
        self._emit(Kind.STRING, _unescape(string.group(1)), string.end())

    def _slash(self, lexeme: re.Match[str]) -> None:
        if not self._regex_allowed():
            self._emit(Kind.PUNCTUATION, "/", lexeme.end())
            return
        regex = REGEX.match(self.source, self.position)
        if regex is None:
            raise ScriptSyntaxError("a regex reaches the end of its line", self.line)
        self._emit(Kind.REGEX, regex.group(1), regex.end())

    def _regex_allowed(self) -> bool:
        """A `/` starts a regex after nothing, after punctuation but `)` and `]`, after a keyword that takes an operand and
        where a template hole begins; after anything else it divides."""
        if not self.tokens:
            return True
        previous = self.tokens[-1]
        if previous.kind is Kind.PUNCTUATION:
            return previous.value not in NON_REGEX_PUNCTUATION
        if previous.kind is Kind.IDENTIFIER:
            return previous.value in REGEX_KEYWORDS
        return previous.kind is Kind.TEMPLATE

    def _template_start(self, lexeme: re.Match[str]) -> None:
        start_line = self.line
        self._consume(lexeme.end())
        self._template_chunk(start_line, opening=True)

    def _template_chunk(self, start_line: int, *, opening: bool) -> None:
        """The text of a template up to its closing backtick or the next `${`; a hole pushes the template's line and, like the
        first one, is a boundary token after which a `/` starts a regex."""
        chunk = self._first(TEMPLATE_CHUNK)
        ending = self.source[chunk.end() : chunk.end() + 2]
        if ending[:1] == "`":
            if opening:
                self.tokens.append(Token(Kind.STRING, _unescape(chunk.group()), start_line))
            self._consume(chunk.end() + 1)
        elif ending == "${":
            self.tokens.append(Token(Kind.TEMPLATE, "", start_line))
            self.braces.append(start_line)
            self._consume(chunk.end() + 2)
        else:
            raise ScriptSyntaxError("a template or its ${ hole is not closed", start_line)

    def _other(self, lexeme: re.Match[str]) -> None:
        character = lexeme.group()
        if character == "{":
            self.braces.append(None)
        elif character == "}" and self.braces:
            template_line = self.braces.pop()
            if template_line is not None:
                self._consume(lexeme.end())
                self._template_chunk(template_line, opening=False)
                return
        self._emit(Kind.PUNCTUATION, character, lexeme.end())


def tokenize(source: str) -> list[Token]:
    """The tokens of a script in source order; raises `ScriptSyntaxError` for a string, template, hole, regex or block comment
    that does not close. Unbalanced brackets and any other character are no refusal."""
    return _Lexer(source).run()
