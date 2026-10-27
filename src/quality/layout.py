import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath

from quality.config import CONFIG_PATH, LayoutAllowance, LayoutRule
from quality.report import Finding

EXECUTABLE_MODE = "100755"
LINK_MODES = frozenset({"120000", "160000"})
# Names a tool finds by this exact name; the list changes with a quality version, like the root base list.
TOOL_MANDATED_NAMES = frozenset({"LICENSE", "Dockerfile", "Makefile", "CODEOWNERS"})
SCRIPTS_DIRECTORY = "scripts"
TESTS_DIRECTORY = "tests"
SCRIPT_SUFFIXES = frozenset({".sh", ".py", ".mjs"})
SCRIPT_NAME_SHAPE = re.compile(r"[a-z0-9]+(_[a-z0-9]+)+")
SHEBANG = b"#!"
HEADER_LINES = 3
SLASH_COMMENT_SUFFIX = ".mjs"
DOCSTRING_OPENER = re.compile(r"""[rRuU]?(\"\"\"|''')""")
ENCODING_DIRECTIVE = re.compile(r"#.*?coding[:=]")
# A comment a tool reads, not a person: shellcheck, an encoding line, noqa, type checkers, ruff, eslint.
TOOL_DIRECTIVE = re.compile(r"(#|//|/\*)\s*(shellcheck\b|-\*-|coding[:=]|noqa\b|type:|pyright:|ruff:|eslint)")
PEP_723_START = "# /// script"
PEP_723_END = "# ///"
PROJECT_FILE = "pyproject.toml"
SOURCE_DIRECTORY = "src"
INIT_FILE = "__init__.py"
CONFTEST_FILE = "conftest.py"
HELPER_DIRECTORIES = frozenset({"support", "fixtures"})
FIXTURES_DIRECTORY = "fixtures"
END_TO_END_DIRECTORY = "e2e"


def check_file_suffix(modes: Mapping[str, str], allowances: Sequence[LayoutAllowance]) -> list[Finding]:
    """Every tracked file without a suffix is executable, a symlink or gitlink, a dotfile, tool-mandated or declared;
    a declaration of an untracked path is a finding too. `modes` maps each tracked path to its index mode. Findings are
    sorted by path."""
    declared = _declared_paths(allowances, LayoutRule.FILE_SUFFIX)
    findings = [_suffixless(path) for path, mode in modes.items() if path not in declared and _lacks_suffix(path, mode)]
    findings += [_stale_declaration(path, LayoutRule.FILE_SUFFIX) for path in declared - modes.keys()]
    return sorted(findings, key=lambda finding: finding.path)


def _declared_paths(allowances: Sequence[LayoutAllowance], rule: LayoutRule) -> set[str]:
    return {allowance.path for allowance in allowances if allowance.rule is rule}


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


def _stale_declaration(path: str, rule: LayoutRule) -> Finding:
    return Finding(
        rule.value, path, None,
        f"declared in {CONFIG_PATH} with [[layout.allow]], but the repository tracks no such file.",
        f"delete its [[layout.allow]] entry from {CONFIG_PATH}.",
    )


def script_candidates(modes: Mapping[str, str]) -> list[str]:
    """The tracked paths whose content may decide whether they are scripts: no link and nothing under a tests folder."""
    return [path for path, mode in modes.items() if mode not in LINK_MODES and not _under_tests(path)]


def check_script_name(
    modes: Mapping[str, str], contents: Mapping[str, bytes], allowances: Sequence[LayoutAllowance]
) -> list[Finding]:
    """A script is named `<word>_<word>...` in lower-case letters and digits and lies under a `scripts/` folder, unless
    its name is tool-mandated or a dotfile, or it is declared. `contents` holds the bytes of `script_candidates`."""
    return _judged_scripts(LayoutRule.SCRIPT_NAME, modes, contents, allowances, _name_problem)


def check_script_header(
    modes: Mapping[str, str], contents: Mapping[str, bytes], allowances: Sequence[LayoutAllowance]
) -> list[Finding]:
    """A script has a comment or docstring, no tool directive, within its first three non-blank lines after the shebang
    and a PEP 723 block, unless it is declared. Presence only, never content."""
    return _judged_scripts(LayoutRule.SCRIPT_HEADER, modes, contents, allowances, _header_problem)


def _judged_scripts(
    rule: LayoutRule,
    modes: Mapping[str, str],
    contents: Mapping[str, bytes],
    allowances: Sequence[LayoutAllowance],
    problem_of: Callable[[str, bytes], tuple[str, str] | None],
) -> list[Finding]:
    declared = _declared_paths(allowances, rule)
    findings = []
    for path, mode in modes.items():
        content = contents.get(path)
        if content is None or path in declared or not _is_script(path, mode, content):
            continue
        if problem := problem_of(path, content):
            findings.append(Finding(rule.value, path, None, *problem))
    findings += [_stale_declaration(path, rule) for path in declared - modes.keys()]
    return sorted(findings, key=lambda finding: finding.path)


def _under_tests(path: str) -> bool:
    return TESTS_DIRECTORY in PurePosixPath(path).parent.parts


def _is_script(path: str, mode: str, content: bytes) -> bool:
    if mode in LINK_MODES or _under_tests(path):
        return False
    pure = PurePosixPath(path)
    in_scripts_folder = SCRIPTS_DIRECTORY in pure.parent.parts and pure.suffix in SCRIPT_SUFFIXES
    return mode == EXECUTABLE_MODE or content.startswith(SHEBANG) or in_scripts_folder


def _name_problem(path: str, _content: bytes) -> tuple[str, str] | None:
    pure = PurePosixPath(path)
    if pure.name.startswith(".") or pure.name in TOOL_MANDATED_NAMES:
        return None
    problems = []
    if not SCRIPT_NAME_SHAPE.fullmatch(pure.stem):
        problems.append("its name is not snake_case with at least two words (lower-case letters and digits)")
    if SCRIPTS_DIRECTORY not in pure.parent.parts:
        problems.append(f"it lies outside a {SCRIPTS_DIRECTORY}/ folder")
    if not problems:
        return None
    return (
        f"a script that breaks the naming rule: {'; '.join(problems)}.",
        (f"rename or move it, or declare it in {CONFIG_PATH} with [[layout.allow]], its path, the rule script-name "
        "and a reason."),
    )


def _header_problem(path: str, content: bytes) -> tuple[str, str] | None:
    markers = ("//", "/*") if PurePosixPath(path).suffix == SLASH_COMMENT_SUFFIX else ("#",)
    if any(_explains(line, markers) for line in _first_lines_after_preamble(content)):
        return None
    return (
        f"a script with no comment or docstring within its first {HEADER_LINES} non-blank lines after the shebang.",
        (f"say what it does in a comment or docstring at the top, or declare it in {CONFIG_PATH} with [[layout.allow]], "
         "its path, the rule script-header and a reason."),
    )


def _first_lines_after_preamble(content: bytes) -> list[str]:
    lines = content.decode("utf-8", errors="replace").splitlines()
    if lines and lines[0].startswith(SHEBANG.decode()):
        lines = lines[1:]
    stripped = [line.strip() for line in lines if line.strip()]
    encodings = 0
    while encodings < len(stripped) and ENCODING_DIRECTIVE.match(stripped[encodings]):
        encodings += 1
    block = stripped[encodings:]
    if block and block[0] == PEP_723_START and PEP_723_END in block[1:]:
        stripped = stripped[:encodings] + block[block.index(PEP_723_END, 1) + 1 :]
    return stripped[:HEADER_LINES]


def _explains(line: str, comment_markers: tuple[str, ...]) -> bool:
    if DOCSTRING_OPENER.match(line):
        return True
    return line.startswith(comment_markers) and not TOOL_DIRECTIVE.match(line)


@dataclass(frozen=True)
class PythonProject:
    """A directory with a tracked `pyproject.toml` and the packages under its `src/`: the directories that hold a
    tracked `.py` at any depth."""

    directory: str
    packages: tuple[str, ...]

    @property
    def tests_directory(self) -> PurePosixPath:
        return PurePosixPath(self.directory) / TESTS_DIRECTORY


def python_projects(paths: Iterable[str]) -> list[PythonProject]:
    """Every project: the directory of each tracked `pyproject.toml` that lies in no other project's `tests/`. The
    file's content is never read. Sorted by directory."""
    tracked = [PurePosixPath(path) for path in paths]
    directories = sorted(path.parent for path in tracked if path.name == PROJECT_FILE)
    kept = [directory for directory in directories if not any(_inside_tests(directory, other) for other in directories)]
    return [PythonProject(str(directory), _packages(directory, tracked)) for directory in kept]


def _inside_tests(directory: PurePosixPath, other: PurePosixPath) -> bool:
    tests = other / TESTS_DIRECTORY
    return directory == tests or tests in directory.parents


def _packages(directory: PurePosixPath, tracked: Sequence[PurePosixPath]) -> tuple[str, ...]:
    source = directory / SOURCE_DIRECTORY
    below = [path.relative_to(source).parts for path in tracked if path.suffix == ".py" and source in path.parents]
    return tuple(sorted({parts[0] for parts in below if len(parts) > 1}))


def check_test_layout(modes: Mapping[str, str], allowances: Sequence[LayoutAllowance]) -> list[Finding]:
    """Every tracked path under a judged project's `tests/` is a `test_*.py`, a `conftest.py`, a file in a `support/` or
    `fixtures/` folder or a file in the top-level `e2e/`, and no `__init__.py` lies there outside `fixtures/`; a
    declaration of an untracked path is a finding too. A project is judged when it has a package."""
    declared = _declared_paths(allowances, LayoutRule.TEST_LAYOUT)
    judged = [project.tests_directory for project in python_projects(modes) if project.packages]
    findings = []
    for path in modes:
        pure = PurePosixPath(path)
        if path not in declared and any(tests in pure.parents for tests in judged):
            findings += _test_layout_findings(pure, next(tests for tests in judged if tests in pure.parents))
    findings += [_stale_declaration(path, LayoutRule.TEST_LAYOUT) for path in declared - modes.keys()]
    return sorted(findings, key=lambda finding: finding.path)


def _test_layout_findings(path: PurePosixPath, tests: PurePosixPath) -> list[Finding]:
    inside = path.relative_to(tests).parts
    folders = inside[:-1]
    if path.name == INIT_FILE and FIXTURES_DIRECTORY not in folders:
        return [_init_under_tests(str(path))]
    allowed = (
        (path.name.startswith("test_") and path.suffix == ".py")
        or path.name == CONFTEST_FILE
        or bool(HELPER_DIRECTORIES.intersection(folders))
        or (folders[:1] == (END_TO_END_DIRECTORY,))
    )
    return [] if allowed else [_stray_under_tests(str(path))]


def _init_under_tests(path: str) -> Finding:
    return Finding(
        LayoutRule.TEST_LAYOUT.value, path, None,
        "an __init__.py under tests/, which pytest's importlib mode does not need.",
        f"delete it, or declare it in {CONFIG_PATH} with [[layout.allow]], its path, the rule test-layout and a reason.",
    )


def _stray_under_tests(path: str) -> Finding:
    return Finding(
        LayoutRule.TEST_LAYOUT.value, path, None,
        "a file under tests/ that is no test_*.py or conftest.py and lies in no support/, fixtures/ or top-level "
        "e2e/ folder.",
        "move it to a support/ folder (a helper) or a fixtures/ folder (data), or declare it in "
        f"{CONFIG_PATH} with [[layout.allow]], its path, the rule test-layout and a reason.",
    )
