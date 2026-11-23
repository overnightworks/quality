"""The `vitest-random` rule: a project that depends on vitest shuffles the files and tests of its root config, with a seed
drawn at run time. Vitest runs in a fixed order unless `test.sequence.shuffle` says otherwise and prints its seed whenever it
shuffles, so the config alone decides R6 for every run: CI, local and IDE.

A config is read as tokens, never run: the exported object (the argument of `defineConfig(`, arrow form included, or the
`export default` object) holds a `test` object with a `sequence` object, and only that `sequence` counts, since vitest 4
ignores a project's own. A key set in an imported module, a spread or `mergeConfig` is not seen."""

from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath

from quality import checkout, layout, repository
from quality.report import CannotJudge, Finding, FindingEvent, RuleResult
from quality.rules import Rule
from quality.typescript_tokens import Kind, ScriptSyntaxError, Token, tokenize

PACKAGE = "vitest"
DEPENDENCY_TABLES = ("dependencies", "devDependencies")
# The names and suffixes vitest looks for, in its order.
CONFIG_STEMS = ("vitest.config", "vite.config")
CONFIG_SUFFIXES = (".ts", ".mts", ".cts", ".js", ".mjs", ".cjs")
FACT = "vitest runs its files and tests in a fixed order"
SHUFFLE_FIX = 'set test.sequence.shuffle: true in {}; vitest then prints Running tests with seed "N"'
SEED_FIX = 'draw test.sequence.seed at run time or drop it in {}; vitest then prints Running tests with seed "N"'
ORDERS = ("files", "tests")
VALUE_ENDS = frozenset({",", "}"})


def check_vitest_random(root: Path, tracked: Sequence[str], package_jsons: Mapping[str, bytes]) -> RuleResult:
    """Each vitest project among the given package.json files, judged by its first tracked config; the extent is the projects."""
    projects = [PurePosixPath(path).parent for path, content in package_jsons.items() if _depends_on_vitest(path, content)]
    configs = {project: _config_of(project, set(tracked)) for project in projects}
    contents = checkout.read_existing(repository.read_bytes, root, checkout.on_disk(root, [name for name in configs.values() if name]))
    findings = [finding for project in projects if (finding := _finding(project, configs[project], contents)) is not None]
    return RuleResult(findings, len(projects), False)


def _depends_on_vitest(path: str, content: bytes) -> bool:
    document = layout.package_document(path, content)
    return any(isinstance(table := document.get(name), dict) and PACKAGE in table for name in DEPENDENCY_TABLES)


def _config_of(project: PurePosixPath, tracked: set[str]) -> str | None:
    names = (str(project / f"{stem}{suffix}") for stem in CONFIG_STEMS for suffix in CONFIG_SUFFIXES)
    return next((name for name in names if name in tracked), None)


def _finding(project: PurePosixPath, config: str | None, contents: Mapping[str, bytes]) -> Finding | None:
    path = config or str(project / layout.PACKAGE_FILE)
    fix = SHUFFLE_FIX if config is None or config not in contents else _fix_of(config, contents[config])
    return None if fix is None else Finding(Rule.VITEST_RANDOM, path, None, FACT, fix.format(path), event=FindingEvent.UNSHUFFLED)


def _fix_of(config: str, content: bytes) -> str | None:
    """The fix a config needs, None where it shuffles both orders with no fixed seed."""
    try:
        reading = _Config(tokenize(content.decode("utf-8")))
    except (UnicodeDecodeError, ScriptSyntaxError) as error:
        raise CannotJudge(f"{config} cannot be read as a script ({error})") from error
    if not reading.shuffles() or reading.turns_shuffle_off():
        return SHUFFLE_FIX
    return SEED_FIX if reading.fixes_seed() else None


def _mark(token: Token) -> str:
    """The text of a punctuation token, empty for every other kind."""
    return token.value if token.kind is Kind.PUNCTUATION else ""


class _Config:
    """The tokens of one config, read for the keys of its exported object."""

    def __init__(self, tokens: Sequence[Token]) -> None:
        self.tokens = tokens

    def shuffles(self) -> bool:
        """Whether the root `test.sequence` shuffles both orders: `shuffle: true` or an object that holds both as `true`."""
        at = self._sequence().get("shuffle")
        if at is None:
            return False
        orders = self._members(at)
        return self._is_value(at, Kind.IDENTIFIER, "true") or all(
            order in orders and self._is_value(orders[order], Kind.IDENTIFIER, "true") for order in ORDERS
        )

    def fixes_seed(self) -> bool:
        """Whether the root `test.sequence` holds a `seed` that is a number literal."""
        at = self._sequence().get("seed")
        return at is not None and self._is_value(at, Kind.NUMBER)

    def turns_shuffle_off(self) -> bool:
        """Whether `shuffle: false` stands anywhere in the file."""
        return any(
            token.value == "shuffle" and self._is_mark(index + 1, ":") and self._is_value(index + 2, Kind.IDENTIFIER, "false")
            for index, token in enumerate(self.tokens)
        )

    def _sequence(self) -> dict[str, int]:
        root = self._members(self._exported_object())
        test = self._members(root.get("test"))
        return self._members(test.get("sequence"))

    def _exported_object(self) -> int | None:
        """The index of the `{` of the exported config object: after `export default`, else after `defineConfig(`."""
        words = [token.value for token in self.tokens]
        for anchor in (["export", "default"], ["defineConfig", "("]):
            start = next((index for index in range(len(words)) if words[index : index + 2] == anchor), None)
            if start is not None:
                return self._body_from(self._after_define_config(start + 2))
        return None

    def _after_define_config(self, start: int) -> int:
        """`start` moved past a `defineConfig(` that opens there."""
        if self.tokens[start : start + 1] and self.tokens[start].value == "defineConfig" and self._is_mark(start + 1, "("):
            return start + 2
        return start

    def _body_from(self, start: int) -> int | None:
        """The `{` that starts at `start` or that the body of an arrow function starts with, `(` around it allowed."""
        if self._is_mark(start, "{"):
            return start
        depth = 0
        for index in range(start, len(self.tokens)):
            word = self.tokens[index].value
            if self._is_mark(index, "("):
                depth += 1
            elif self._is_mark(index, ")"):
                depth -= 1
            elif depth == 0 and self._is_mark(index, ">") and self._is_mark(index - 1, "="):
                body = index + 1
                while self._is_mark(body, "("):
                    body += 1
                return body if self._is_mark(body, "{") else None
            elif depth == 0 and word != "async" and not self._is_mark(index, "="):
                return None
        return None

    def _is_mark(self, index: int, text: str) -> bool:
        return 0 <= index < len(self.tokens) and _mark(self.tokens[index]) == text

    def _members(self, opening: int | None) -> dict[str, int]:
        """Each direct key of the object literal whose `{` stands at `opening`, with the index of its value; empty where
        `opening` holds no object."""
        found: dict[str, int] = {}
        if opening is None or not self._is_mark(opening, "{"):
            return found
        depth = 0
        for index in range(opening, len(self.tokens)):
            mark = _mark(self.tokens[index])
            if mark and mark in "([{":
                depth += 1
            elif mark and mark in ")]}":
                depth -= 1
                if depth == 0:
                    break
            elif depth == 1 and self._is_key(index):
                found[self.tokens[index].value] = index + 2
        return found

    def _is_key(self, index: int) -> bool:
        return (
            self.tokens[index].kind in (Kind.IDENTIFIER, Kind.STRING)
            and (self._is_mark(index - 1, "{") or self._is_mark(index - 1, ","))
            and self._is_mark(index + 1, ":")
        )

    def _is_value(self, index: int, kind: Kind, text: str | None = None) -> bool:
        """Whether the value at `index` is the one token of `kind` (spelled `text`) before the next `,` or `}`."""
        if index >= len(self.tokens) or self.tokens[index].kind is not kind or text not in (None, self.tokens[index].value):
            return False
        return index + 1 < len(self.tokens) and _mark(self.tokens[index + 1]) in VALUE_ENDS
