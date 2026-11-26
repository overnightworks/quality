"""The `root-layout` rule: every tracked root entry is on quality's base list, accepted by its case or declared with
`[[root.allow]]`. A case is a directory that a tracked name shows the shape of; the rule reads names, never content."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from quality.config_allowances import ROOT_ALLOWANCE_HEADING, RootAllowance
from quality.config_entries import CONFIG_PATH
from quality.layout import PACKAGE_FILE, PROJECT_FILE, PROJECT_FILES
from quality.report import Finding, FindingEvent
from quality.rules import Rule

# A directory is written with its trailing slash, as `[[root.allow]]` writes it.
ROOT_BASE_FILES = frozenset({
    "README.md", "AGENTS.md", "CLAUDE.md", "HEART.md", "LICENSE",
    ".gitignore", ".gitattributes", ".gitmodules",
    "pyproject.toml", "uv.lock", ".python-version",
    "package.json", "pnpm-lock.yaml", "package-lock.json",
    "Cargo.toml", "Cargo.lock", "rust-toolchain.toml",
    "Dockerfile", ".dockerignore", "compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml",
    "sonar-project.properties", ".importlinter", ".pre-commit-config.yaml",
    "tsconfig.json", "vitest.config.ts", "Makefile", "mise.toml", "mise.lock", "pnpm-workspace.yaml", "alembic.ini", ".sops.yaml",
})
ROOT_BASE_DIRECTORIES = frozenset({
    ".github/", ".aco/", "quality/", "src/", "tests/", "docs/", "scripts/",
    ".claude/", ".claude-plugin/", ".codex/", ".agents/",
})
ROOT_BASE_ENTRIES = ROOT_BASE_FILES | ROOT_BASE_DIRECTORIES

FLUX_DIRECTORY = "flux-system"


@dataclass(frozen=True)
class RootCase:
    """A root directory accepted where `met` holds over the tracked paths; `condition` finishes "accepted where"."""

    entry: str
    condition: str
    met: Callable[[frozenset[str]], bool]


def _tracks(path: str) -> Callable[[frozenset[str]], bool]:
    return lambda tracked: path in tracked


def _directory_of_projects(directory: str) -> Callable[[frozenset[str]], bool]:
    """Each child folder of `directory` that holds a tracked file holds a project manifest directly, and at least one does."""

    def met(tracked: frozenset[str]) -> bool:
        children = {parts[1] for parts in (path.split("/") for path in tracked) if len(parts) > 2 and parts[0] + "/" == directory}
        return bool(children) and all(any(f"{directory}{child}/{name}" in tracked for name in PROJECT_FILES) for child in children)

    return met


def _flux_bootstrap(tracked: frozenset[str]) -> bool:
    return any(len(parts) > 3 and parts[0] == "clusters" and parts[2] == FLUX_DIRECTORY for parts in (path.split("/") for path in tracked))


_PROJECTS_CONDITION = f"it holds folders and each holds a {PROJECT_FILE} or {PACKAGE_FILE}"
ROOT_CASES = (
    RootCase("frontend/", f"frontend/{PACKAGE_FILE} is tracked", _tracks(f"frontend/{PACKAGE_FILE}")),
    RootCase("legal/", "legal/datenschutz.html is tracked", _tracks("legal/datenschutz.html")),
    RootCase("packages/", _PROJECTS_CONDITION, _directory_of_projects("packages/")),
    RootCase("providers/", _PROJECTS_CONDITION, _directory_of_projects("providers/")),
    *(
        RootCase(entry, f"a file under clusters/<name>/{FLUX_DIRECTORY}/ is tracked", _flux_bootstrap)
        for entry in ("clusters/", "infrastructure/", "apps/")
    ),
)

_BASE_SPELLINGS = {entry.casefold(): entry for entry in ROOT_BASE_ENTRIES}
_CASES_BY_ENTRY = {case.entry: case for case in ROOT_CASES}
_CASES_BY_SPELLING = {case.entry.casefold(): case for case in ROOT_CASES}


def check_root_layout(tracked: list[str], allowances: Sequence[RootAllowance]) -> list[Finding]:
    """Every tracked root entry, a directory with its trailing `/`, is on the base list, accepted by its case or declared;
    a declaration that matches no tracked root entry is a finding too. Findings are sorted by entry."""
    entries = {name.partition("/")[0] + "/" if "/" in name else name for name in tracked}
    declared = {allowance.entry for allowance in allowances}
    tracked_paths = frozenset(tracked)
    findings = [
        _unlisted_root_entry(entry)
        for entry in entries - ROOT_BASE_ENTRIES - declared
        if not _accepted_by_case(entry, tracked_paths)
    ]
    findings += [_stale_root_declaration(entry, entries) for entry in declared - entries]
    return sorted(findings, key=lambda finding: finding.path)


def _accepted_by_case(entry: str, tracked: frozenset[str]) -> bool:
    case = _CASES_BY_ENTRY.get(entry)
    return case is not None and case.met(tracked)


def _unlisted_fact(entry: str) -> str:
    facts = ["not on quality's base list"]
    if base_spelling := _BASE_SPELLINGS.get(entry.casefold()):
        facts.append(f"the list spells it {base_spelling}")
    if case := _CASES_BY_SPELLING.get(entry.casefold()):
        if entry != case.entry:
            facts.append(f"its case spells it {case.entry}")
        facts.append(f"accepted where {case.condition}")
    return " · ".join(facts)


def _unlisted_root_entry(entry: str) -> Finding:
    return Finding(
        Rule.ROOT_LAYOUT, entry, None,
        _unlisted_fact(entry),
        f"move it to its owner's directory, or declare it in {CONFIG_PATH} with {ROOT_ALLOWANCE_HEADING}, an entry and a reason",
        event=FindingEvent.UNLISTED,
    )


def _stale_root_declaration(entry: str, tracked_entries: set[str]) -> Finding:
    hint = f" · the repository tracks {entry}/" if f"{entry}/" in tracked_entries else ""
    return Finding(
        Rule.ROOT_LAYOUT, entry, None,
        f"{ROOT_ALLOWANCE_HEADING} matches no tracked root entry{hint}",
        f"delete its {ROOT_ALLOWANCE_HEADING} entry from {CONFIG_PATH}",
        event=FindingEvent.UNUSED,
    )
