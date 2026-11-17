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
HEAD_KEYWORDS = frozenset({"if", "while", "for", "with"})
NON_REGEX_PUNCTUATION = frozenset({")", "]"})
POSTFIX_STEPS = frozenset({"++", "--"})
SKIPPED = re.compile(r"(?P<space>\s+)|(?P<comment>//[^\n\r\u2028\u2029]*|/\*.*?\*/)|(?P<unclosed>/\*)", re.DOTALL)
LEXEME = re.compile(
    r"(?P<word>(?:[^\W\d]|\$)[\w$]*)|(?P<number>\d[\w.]*)|(?P<quote>['\"])|(?P<template>`)|(?P<slash>/)|(?P<other>.)",
    re.DOTALL,
)
STRINGS = {
    quote: re.compile(rf"{quote}((?:[^{quote}\\\r\n]++|\\(?:\r\n|.))*+){quote}", re.DOTALL) for quote in ("'", '"')
}
TEMPLATE_CHUNK = re.compile(r"(?:[^`\\$]++|\\.|\$(?!\{))*+", re.DOTALL)
REGEX_ESCAPE = r"\\[^\r\n\u2028\u2029]"
REGEX_CLASS = rf"\[(?:[^\]\\\r\n\u2028\u2029]++|{REGEX_ESCAPE})*+\]"
REGEX = re.compile(rf"/((?:[^/\\\[\r\n\u2028\u2029]++|{REGEX_ESCAPE}|{REGEX_CLASS})*+)/\w*")
ESCAPE = re.compile(r"\\(?:u\{([0-9a-fA-F]+)\}|u([0-9a-fA-F]{4})|x([0-9a-fA-F]{2})|(\r\n|.))", re.DOTALL)
SIMPLE_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}
LINE_TERMINATORS = frozenset({"\n", "\r", "\r\n", "\u2028", "\u2029"})
LINE_BREAK = re.compile(r"\r\n|[\n\r\u2028\u2029]")
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
        # One entry per open paren: whether it opens the head of an `if`, `while`, `for` or `with`.
        self.parens: list[bool] = []
        # The token indices of the `)` that close such a head; a regex may follow them.
        self.head_closers: set[int] = set()

    def run(self) -> list[Token]:
        handlers = {
            "space": self._skip, "comment": self._skip, "unclosed": self._unclosed_comment, "word": self._word,
            "number": self._number, "quote": self._string, "template": self._template_start, "slash": self._slash,
            "other": self._other,
        }
        while self.position < len(self.source):
            lexeme = SKIPPED.match(self.source, self.position) or self._first(LEXEME)
            handlers[str(lexeme.lastgroup)](lexeme)
        unclosed = [line for line in self.braces if line is not None]
        if unclosed:
            raise ScriptSyntaxError("a template or its ${ hole is not closed", unclosed[0])
        return self.tokens

    def _first(self, pattern: re.Pattern[str]) -> re.Match[str]:
        """The match of a pattern that matches at every position, here: its `other` branch or an empty chunk."""
        return next(pattern.finditer(self.source, self.position))

    def _consume(self, end: int) -> None:
        self.line += len(LINE_BREAK.findall(self.source, self.position, end))
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
        """A `/` starts a regex after nothing, after punctuation but `)`, `]`, a postfix `++` or `--` and a `!` that follows an
        operand (a non-null mark), after a keyword that takes an operand (not after a property of that spelling) and where a
        template hole begins, and after the `)` that closes the head of an `if`, `while`, `for` or `with`; after anything else it
        divides."""
        if not self.tokens:
            return True
        previous = self.tokens[-1]
        if previous.kind is Kind.PUNCTUATION:
            closes_head = len(self.tokens) - 1 in self.head_closers
            return (closes_head or previous.value not in NON_REGEX_PUNCTUATION) and not self._ends_in_postfix_operator()
        if previous.kind is Kind.IDENTIFIER:
            return not self._ends_operand_word(len(self.tokens) - 1)
        return previous.kind is Kind.TEMPLATE

    def _ends_operand_word(self, index: int) -> bool:
        """A name ends an operand, a keyword that takes an operand does not, and a name after a `.` (or `?.`) or a `#` is a
        property or a private field, whatever it is spelled."""
        return self.tokens[index].value not in REGEX_KEYWORDS or self._is_member(index)

    def _is_member(self, index: int) -> bool:
        """Whether the name at `index` follows a `.` (or `?.`) or a `#`; a `.` that closes a spread's three dots is no member
        access."""
        before = self.tokens[max(index - 2, 0) : index]
        marks = [token.kind is Kind.PUNCTUATION and token.value for token in before]
        return marks[-1:] == ["#"] or (marks[-1:] == ["."] and marks != [".", "."])

    def _opens_statement_head(self) -> bool:
        """Whether a `(` now follows `if`, `while`, `for` or `for await`, not as a property of that spelling."""
        words = [token.value if token.kind is Kind.IDENTIFIER else "" for token in self.tokens[-2:]]
        index = len(self.tokens) - 1
        if words[-2:] == ["for", "await"]:
            index -= 1
        return index >= 0 and self.tokens[index].kind is Kind.IDENTIFIER and self.tokens[index].value in HEAD_KEYWORDS and not self._is_member(index)

    def _ends_in_postfix_operator(self) -> bool:
        """`++` or `--` (a regex never follows a prefix one) or a `!` right after an identifier, `)` or `]`, the TypeScript
        non-null mark; a `!` after anything else is a prefix not."""
        last = self.tokens[-1].value
        before = self.tokens[-2] if len(self.tokens) > 1 else None
        if before is None:
            return False
        if last in POSTFIX_STEPS:
            return True
        if last != "!":
            return False
        if before.kind is Kind.PUNCTUATION:
            return before.value in NON_REGEX_PUNCTUATION
        return before.kind is Kind.IDENTIFIER and self._ends_operand_word(len(self.tokens) - 2)

    def _template_start(self, lexeme: re.Match[str]) -> None:
        start_line = self.line
        self._consume(lexeme.end())
        self._template_chunk(start_line, opening=True)

    def _template_chunk(self, start_line: int, *, opening: bool) -> None:
        """The text of a template up to its closing backtick or the next `${`; a hole pushes the template's line and, like the
        first one, is a boundary token after which a `/` starts a regex."""
        chunk = self._first(TEMPLATE_CHUNK)
        ending = self.source[chunk.end() : chunk.end() + 2]
        if ending.startswith("`"):
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
        step = self.source[self.position : self.position + 2]
        if step in POSTFIX_STEPS:
            self._emit(Kind.PUNCTUATION, step, self.position + 2)
            return
        if character == "(":
            self.parens.append(self._opens_statement_head())
        elif character == ")" and self.parens and self.parens.pop():
            self.head_closers.add(len(self.tokens))
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
