"""The recipe lines that a root `Makefile` gives the goals a `make` call names, read by the make manual's rules for a plain file.

A rule's recipe is the tab lines under it; a conditional, a define block and a rule the scan cannot name are not read, and a
file that holds a directive the scan cannot see through is no call as a whole. The text goes in and the lines come out; no
file is read here. The shell words of a recipe are read by `run_script`, the only quality module this one imports;
`catalog_rules` is its only caller."""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from quality.run_script import ScriptLine, command_words, logical_lines, segments

MAKE = "make"
RECIPE_START = "\t"
PREFIX_CHARACTERS = " \t@+-"
IGNORED_FAILURE_PREFIX = "-"
KEYWORD = re.compile(r"[a-z]+")
CONDITIONALS = frozenset({"ifeq", "ifneq", "ifdef", "ifndef"})
REFUSED_TARGETS = re.compile(r"(?:\.IGNORE|\.RECIPEPREFIX|\.ONESHELL)(?![\w.-])")
REFUSED_INCLUDE = re.compile(r"-?s?include\b")
REFUSED_MAKEFLAGS = re.compile(r"MAKEFLAGS\s*[:+?!]?=")
VARIABLE_WORD = re.compile(r"\w+=.*", re.DOTALL)
MAKE_SUBSTITUTION = re.compile(r"\$\$|\$\(MAKE\)|\$\{MAKE\}")
PATTERN_CHARACTERS = ("%", "$")


@dataclass(frozen=True)
class RecipeLine:
    """A recipe line the goals reach: its command without the tab and its prefixes, and the file line it starts on."""

    text: str
    number: int


@dataclass
class _Rule:
    """A rule line: the names it defines (none when the rule is not read), its prerequisites, whether it is a double-colon rule,
    and the logical lines under it."""

    names: tuple[str, ...]
    prerequisites: tuple[str, ...]
    double: bool
    recipe: list[ScriptLine] = field(default_factory=list)


@dataclass
class _Scan:
    """The state of the walk over a file's logical lines."""

    rules: list[_Rule] = field(default_factory=list)
    active: _Rule | None = None
    depth: int = 0
    in_define: bool = False

    def feed(self, line: ScriptLine) -> None:
        if not line.text.strip():
            return
        if line.text.startswith(RECIPE_START):
            if self.active is not None:
                self.active.recipe.append(line)
            return
        self._feed_directive_or_rule(line)

    def _feed_directive_or_rule(self, line: ScriptLine) -> None:
        keyword = _keyword(line.text)
        if self.in_define:
            self.in_define = keyword != "endef"
            return
        if keyword == "define":
            self.active = None
            self.in_define = True
        elif keyword in CONDITIONALS:
            self.active = None
            self.depth += 1
        elif keyword == "endif":
            self.active = None
            self.depth = max(self.depth - 1, 0)
        elif keyword == "else":
            self.active = None
        elif self.depth:
            return
        else:
            self._start_rule(line.text.strip())

    def _start_rule(self, text: str) -> None:
        rule = _rule_from(text)
        self.active = rule
        if rule is not None:
            self.rules.append(rule)


def make_goals(command: Sequence[str]) -> tuple[str, ...]:
    """The goals of a `make` call, from its command word on; no call, a flag or a word with `$` makes it none, and so does no goal."""
    if not command or PurePosixPath(command[0]).name != MAKE:
        return ()
    goals: list[str] = []
    for word in command[1:]:
        if word.startswith("-"):
            return ()
        if VARIABLE_WORD.fullmatch(word):
            continue
        if "$" in word or "=" in word:
            return ()
        goals.append(word)
    return tuple(goals)


def reached_recipe_lines(text: str, goals: Sequence[str]) -> tuple[RecipeLine, ...]:
    """The recipe lines of the goals and of the targets they reach, each target followed once; empty when nothing is read."""
    lines = list(logical_lines([(line, number) for number, line in enumerate(text.split("\n"), start=1)]))
    if any(_is_refused_directive(line.text) for line in lines):
        return ()
    scan = _Scan()
    for line in lines:
        scan.feed(line)
    found: list[RecipeLine] = []
    visited: set[str] = set()
    for goal in goals:
        _follow(goal, scan.rules, visited, found)
    return tuple(found)


def _follow(name: str, rules: Sequence[_Rule], visited: set[str], found: list[RecipeLine]) -> None:
    """Adds the recipe of `name`, then that of its prerequisites in order, then that of the goals its recipe's make calls name."""
    if name in visited:
        return
    visited.add(name)
    named = [rule for rule in rules if name in rule.names]
    recipe = _recipe_of(named)
    found.extend(recipe)
    for prerequisite in (word for rule in named for word in rule.prerequisites):
        _follow(prerequisite, rules, visited, found)
    for line in recipe:
        for goal in goals_in_line(line.text):
            _follow(goal, rules, visited, found)


def _recipe_of(named: Sequence[_Rule]) -> tuple[RecipeLine, ...]:
    """The recipe a target has: none when a rule of it is double-colon or two rules give it recipe lines, else the one given."""
    if any(rule.double for rule in named):
        return ()
    recipes = [rule.recipe for rule in named if rule.recipe]
    if len(recipes) != 1:
        return ()
    return tuple(line for line in (_recipe_line(raw) for raw in recipes[0]) if line is not None)


def _recipe_line(raw: ScriptLine) -> RecipeLine | None:
    """The command of a recipe line without its tab and its prefixes; none when a `-` makes its failure ignored, or nothing is left."""
    body = raw.text[len(RECIPE_START) :]
    command = body.lstrip(PREFIX_CHARACTERS)
    if IGNORED_FAILURE_PREFIX in body[: len(body) - len(command)] or not command.strip():
        return None
    return RecipeLine(MAKE_SUBSTITUTION.sub(_substitute, command).strip(), raw.first)


def _substitute(match: re.Match[str]) -> str:
    return "$" if match.group() == "$$" else MAKE


def goals_in_line(text: str) -> list[str]:
    try:
        parts = segments(text)
    except ValueError:
        return []
    return [goal for part in parts for goal in make_goals(command_words(part))]


def _rule_from(text: str) -> _Rule | None:
    """The rule a line starts: its names and prerequisites. None when the line is no rule (an assignment, or a line with no
    colon); a rule whose names or prerequisites hold a pattern or a variable is one with no names, so it defines nothing."""
    colon = text.find(":")
    head = text[:colon]
    if colon < 0 or "=" in head:
        return None
    rest = text[colon + 1 :]
    double = rest.startswith(":")
    if double:
        rest = rest[1:]
    prerequisite_text = rest.split(";", 1)[0]
    if rest.startswith("=") or "=" in prerequisite_text:
        return None
    names = tuple(head.split())
    prerequisites = tuple(word for word in prerequisite_text.split() if word != "|")
    if not names or _holds_pattern((*names, *prerequisites)):
        names = ()
    return _Rule(names, prerequisites, double)


def _holds_pattern(words: Sequence[str]) -> bool:
    return any(character in word for word in words for character in PATTERN_CHARACTERS)


def _keyword(text: str) -> str:
    match = KEYWORD.match(text.strip())
    return match.group() if match else ""


def _is_refused_directive(text: str) -> bool:
    """Whether a line the file holds makes the whole file no call: `.IGNORE`, `.RECIPEPREFIX`, `.ONESHELL`, an include or a
    `MAKEFLAGS` assignment, wherever it stands; a recipe line is never one."""
    if text.startswith(RECIPE_START):
        return False
    code = text.strip()
    return bool(REFUSED_TARGETS.match(code) or REFUSED_INCLUDE.match(code) or REFUSED_MAKEFLAGS.match(code))
