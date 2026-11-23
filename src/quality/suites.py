"""A repository's tests: the script test call that `spec-citation` reads and the fleet report counts."""

from collections.abc import Iterator
from pathlib import PurePosixPath

from quality.ratchet import Language, language_of
from quality.typescript_tokens import Kind, ScriptSyntaxError, Token, tokenize

SCRIPT_TEST_CALLS = frozenset({"it", "test"})
SCRIPT_LANGUAGES = frozenset({Language.TYPESCRIPT, Language.JAVASCRIPT})


def script_titles(tokens: list[Token]) -> Iterator[Token]:
    """The title of each bare `it(` or `test(` call whose first argument is a string or a template without a hole."""
    for before, name, opening, title in zip([None, *tokens], tokens, tokens[1:], tokens[2:], strict=False):
        is_bare = before is None or before.value != "."
        if is_bare and name.kind is Kind.IDENTIFIER and name.value in SCRIPT_TEST_CALLS and opening.value == "(" and title.kind is Kind.STRING:
            yield title


def is_script_test(path: str) -> bool:
    language = language_of(path)
    return language in SCRIPT_LANGUAGES and language.is_test_file(PurePosixPath(path))


def read_script_tests(tracked: list[str], sources: dict[str, bytes]) -> tuple[dict[str, list[Token]], list[str]]:
    """The tokens of each tracked TypeScript and JavaScript test file, and the files that are absent from the tree,
    not UTF-8 or refused by the lexer."""
    read: dict[str, list[Token]] = {}
    unread: list[str] = []
    for path in (path for path in tracked if is_script_test(path)):
        if path not in sources:
            unread.append(path)
            continue
        try:
            read[path] = tokenize(sources[path].decode("utf-8"))
        except (UnicodeDecodeError, ScriptSyntaxError):
            unread.append(path)
    return read, unread
