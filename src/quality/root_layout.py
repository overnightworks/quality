"""The `root-layout` rule: every tracked root entry is on quality's base list or declared with `[[root.allow]]`."""

from collections.abc import Sequence

from quality.config import CONFIG_PATH, RootAllowance
from quality.report import Finding

ROOT_LAYOUT = "root-layout"

# A directory is written with its trailing slash, as `[[root.allow]]` writes it.
ROOT_BASE_FILES = frozenset({
    "README.md", "AGENTS.md", "CLAUDE.md", "HEART.md", "LICENSE",
    ".gitignore", ".gitattributes", ".gitmodules",
    "pyproject.toml", "uv.lock", ".python-version",
    "package.json", "pnpm-lock.yaml", "package-lock.json",
    "Cargo.toml", "Cargo.lock", "rust-toolchain.toml",
    "Dockerfile", ".dockerignore", "compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml",
    "sonar-project.properties", ".importlinter", ".pre-commit-config.yaml",
})
ROOT_BASE_DIRECTORIES = frozenset({".github/", ".aco/", "quality/", "src/", "tests/", "docs/", "scripts/"})
ROOT_BASE_ENTRIES = ROOT_BASE_FILES | ROOT_BASE_DIRECTORIES


def check_root_layout(tracked: list[str], allowances: Sequence[RootAllowance]) -> list[Finding]:
    """Every tracked root entry, a directory with its trailing `/`, is on the base list or declared; a declaration that
    matches no tracked root entry is a finding too. Findings are sorted by entry."""
    entries = {name.partition("/")[0] + "/" if "/" in name else name for name in tracked}
    declared = {allowance.entry for allowance in allowances}
    base_spelling = {entry.casefold(): entry for entry in ROOT_BASE_ENTRIES}
    findings = [
        _unlisted_root_entry(entry, base_spelling.get(entry.casefold()))
        for entry in entries - ROOT_BASE_ENTRIES - declared
    ]
    findings += [_stale_root_declaration(entry, entries) for entry in declared - entries]
    return sorted(findings, key=lambda finding: finding.path)


def _unlisted_root_entry(entry: str, base_spelling: str | None) -> Finding:
    hint = f" (the base list knows {base_spelling})" if base_spelling else ""
    return Finding(
        ROOT_LAYOUT, entry, None,
        f"not on quality's base list{hint}.",
        f"move it to its owner's directory, or declare it in {CONFIG_PATH} with [[root.allow]], an entry and a reason.",
    )


def _stale_root_declaration(entry: str, tracked_entries: set[str]) -> Finding:
    hint = f" (the repository tracks {entry}/)" if f"{entry}/" in tracked_entries else ""
    return Finding(
        ROOT_LAYOUT, entry, None,
        f"declared in {CONFIG_PATH} with [[root.allow]], but the repository tracks no such root entry{hint}.",
        f"delete its [[root.allow]] entry from {CONFIG_PATH}.",
    )
