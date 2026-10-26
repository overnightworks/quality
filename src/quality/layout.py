from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath

from quality.config import CONFIG_PATH, LayoutAllowance, LayoutRule
from quality.report import Finding

EXECUTABLE_MODE = "100755"
LINK_MODES = frozenset({"120000", "160000"})
# Names a tool finds by this exact name; the list changes with a quality version, like the root base list.
TOOL_MANDATED_NAMES = frozenset({"LICENSE", "Dockerfile", "Makefile", "CODEOWNERS"})


def check_file_suffix(modes: Mapping[str, str], allowances: Sequence[LayoutAllowance]) -> list[Finding]:
    """Every tracked file without a suffix is executable, a symlink or gitlink, a dotfile, tool-mandated or declared;
    a declaration of an untracked path is a finding too. `modes` maps each tracked path to its index mode. Findings are
    sorted by path."""
    declared = {allowance.path for allowance in allowances if allowance.rule is LayoutRule.FILE_SUFFIX}
    findings = [_suffixless(path) for path, mode in modes.items() if path not in declared and _lacks_suffix(path, mode)]
    findings += [_stale_declaration(path) for path in declared - modes.keys()]
    return sorted(findings, key=lambda finding: finding.path)


def _lacks_suffix(path: str, mode: str) -> bool:
    name = PurePosixPath(path).name
    exempt = mode == EXECUTABLE_MODE or mode in LINK_MODES or name.startswith(".") or name in TOOL_MANDATED_NAMES
    return not exempt and not PurePosixPath(name).suffix


def _suffixless(path: str) -> Finding:
    return Finding(
        LayoutRule.FILE_SUFFIX.value, path, None,
        "a tracked file without a suffix, that is no executable, link, dotfile or tool-mandated name.",
        f"give it the suffix of its format, or declare it in {CONFIG_PATH} with [[layout.allow]], its path, the rule "
        "file-suffix and a reason.",
    )


def _stale_declaration(path: str) -> Finding:
    return Finding(
        LayoutRule.FILE_SUFFIX.value, path, None,
        f"declared in {CONFIG_PATH} with [[layout.allow]], but the repository tracks no such file.",
        f"delete its [[layout.allow]] entry from {CONFIG_PATH}.",
    )
