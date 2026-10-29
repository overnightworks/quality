import ast
import re
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath

from quality import checkout, ratchet
from quality.config import CONFIG_PATH, LayoutAllowance
from quality.report import Finding
from quality.rules import Rule

EXECUTABLE_MODE = "100755"
GITLINK_MODE = "160000"
LINK_MODES = frozenset({"120000", GITLINK_MODE})
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
PACKAGE_FILE = "package.json"
PROJECT_FILES = frozenset({PROJECT_FILE, PACKAGE_FILE})
SOURCE_DIRECTORY = "src"
INIT_FILE = "__init__.py"
CONFTEST_FILE = "conftest.py"
HELPER_DIRECTORIES = frozenset({"support", "fixtures"})
FIXTURES_DIRECTORY = "fixtures"
END_TO_END_DIRECTORY = "e2e"
SUPPORT_DIRECTORY = "support"
WORKFLOWS_TESTS_DIRECTORY = "workflows"
ROOT_DIRECTORY = "."
ROUTE_MARK = "+"
SCRIPT_LANGUAGES = frozenset({ratchet.Language.TYPESCRIPT, ratchet.Language.JAVASCRIPT})
SPEC_WORD = "spec"
FRONTEND_WORD_BOUNDARY = re.compile(r"[-_]|(?<=[a-z0-9])(?=[A-Z])")
PYTHON_WORD_BOUNDARY = re.compile(r"_")
TEST_PREFIX = "test_"
WRAPPED_NAME = re.compile(r"__(.+)__")


def check_file_suffix(modes: Mapping[str, str], allowances: Sequence[LayoutAllowance]) -> list[Finding]:
    """Every tracked file without a suffix is executable, a symlink or gitlink, a dotfile, tool-mandated or declared;
    a declaration of an untracked path is a finding too. `modes` maps each tracked path to its index mode. Findings are
    sorted by path."""
    declared = _declared_paths(allowances, Rule.FILE_SUFFIX)
    findings = [_suffixless(path) for path, mode in modes.items() if path not in declared and _lacks_suffix(path, mode)]
    findings += [_stale_declaration(path, Rule.FILE_SUFFIX) for path in declared - modes.keys()]
    return sorted(findings, key=lambda finding: finding.path)


def _declared_paths(allowances: Sequence[LayoutAllowance], rule: Rule) -> set[str]:
    return {allowance.path for allowance in allowances if allowance.rule is rule}


def _lacks_suffix(path: str, mode: str) -> bool:
    name = PurePosixPath(path).name
    exempt = mode == EXECUTABLE_MODE or mode in LINK_MODES or name.startswith(".") or name in TOOL_MANDATED_NAMES
    return not exempt and not PurePosixPath(name).suffix


def _suffixless(path: str) -> Finding:
    return Finding(
        Rule.FILE_SUFFIX, path, None,
        "a tracked file without a suffix, that is no executable, link, dotfile or tool-mandated name.",
        f"give it the suffix of its format, or declare it in {CONFIG_PATH} with [[layout.allow]], its path, the rule "
        "file-suffix and a reason.",
    )


def _stale_declaration(path: str, rule: Rule) -> Finding:
    return Finding(
        rule, path, None,
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
    return _judged_scripts(Rule.SCRIPT_NAME, modes, contents, allowances, _name_problem)


def check_script_header(
    modes: Mapping[str, str], contents: Mapping[str, bytes], allowances: Sequence[LayoutAllowance]
) -> list[Finding]:
    """A script has a comment or docstring, no tool directive, within its first three non-blank lines after the shebang
    and a PEP 723 block, unless it is declared. Presence only, never content."""
    return _judged_scripts(Rule.SCRIPT_HEADER, modes, contents, allowances, _header_problem)


def _judged_scripts(
    rule: Rule,
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
            findings.append(Finding(rule, path, None, *problem))
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
    return [PythonProject(str(directory), _packages(directory, tracked)) for directory in _project_directories(tracked, PROJECT_FILE)]


def frontend_projects(paths: Iterable[str]) -> list[str]:
    """The directory of each tracked `package.json` that lies in no project's `tests/`, of either kind. The file's
    content is never read. Sorted."""
    return [str(directory) for directory in _project_directories([PurePosixPath(path) for path in paths], PACKAGE_FILE)]


def _project_directories(tracked: Sequence[PurePosixPath], manifest: str) -> list[PurePosixPath]:
    holders = {path.parent for path in tracked if path.name in PROJECT_FILES}
    wanted = sorted({path.parent for path in tracked if path.name == manifest})
    return [directory for directory in wanted if not any(_inside_tests(directory, other) for other in holders)]


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
    `test_*.py` outside `fixtures/` and the top-level `e2e/` lies in the folder that mirrors its module and is named
    `test_<module>[_<aspect>].py`; a declaration of an untracked path is a finding too. A project is judged when it
    has a package. A Vitest test of a frontend project lies beside the file it tests and a Playwright spec in the
    project's top-level `e2e/`."""
    declared = _declared_paths(allowances, Rule.TEST_LAYOUT)
    tracked = [PurePosixPath(path) for path in modes]
    mirrors = {
        project.tests_directory: _ProjectMirror.of(project, tracked)
        for project in python_projects(modes)
        if project.packages
    }
    findings = _frontend_findings(modes, declared, mirrors.keys())
    for path in tracked:
        tests = next((tests for tests in mirrors if tests in path.parents), None)
        if tests is not None and str(path) not in declared:
            findings += _test_layout_findings(path, tests, mirrors[tests])
    findings += [_stale_declaration(path, Rule.TEST_LAYOUT) for path in declared - modes.keys()]
    return sorted(findings, key=lambda finding: finding.path)


def _test_layout_findings(path: PurePosixPath, tests: PurePosixPath, mirror: _ProjectMirror) -> list[Finding]:
    inside = path.relative_to(tests).parts
    folders = inside[:-1]
    if path.name == INIT_FILE and not _in_fixtures(folders):
        return [_init_under_tests(str(path))]
    if _is_test_module(path):
        return _mirror_findings(path, folders, mirror)
    allowed = (
        path.name == CONFTEST_FILE
        or bool(HELPER_DIRECTORIES.intersection(folders))
        or (folders[:1] == (END_TO_END_DIRECTORY,))
    )
    return [] if allowed else [_stray_under_tests(str(path))]


_Problem = tuple[str, str]


@dataclass(frozen=True)
class _Destination:
    """The keys of the folder a test mirrors, and how to call its candidates in a finding."""

    folder: str
    noun: str
    placeholder: str
    keys: frozenset[str]


@dataclass(frozen=True)
class _ProjectMirror:
    """What a project's tests may mirror: the keys of each `src/` package folder and `scripts/` folder, and the
    root's workflows."""

    directory: PurePosixPath
    packages: tuple[str, ...]
    sources: Mapping[PurePosixPath, frozenset[str]]
    scripts: Mapping[PurePosixPath, frozenset[str]]
    workflows: frozenset[str]

    @classmethod
    def of(cls, project: PythonProject, tracked: Sequence[PurePosixPath]) -> _ProjectMirror:
        directory = PurePosixPath(project.directory)
        source, scripts = directory / SOURCE_DIRECTORY, directory / SCRIPTS_DIRECTORY
        return cls(
            directory,
            project.packages,
            _folder_keys([path for path in tracked if path.suffix == ".py" and source in path.parents], source),
            _folder_keys([path for path in tracked if scripts in path.parents], scripts),
            frozenset(
                _key(path.stem.replace("-", "_"))
                for path in tracked
                if checkout.is_workflow(path)
            ),
        )

    def destination(self, folders: tuple[str, ...]) -> tuple[list[_Problem], _Destination | None]:
        if folders[:1] == (SCRIPTS_DIRECTORY,):
            folder = self.directory / SCRIPTS_DIRECTORY / PurePosixPath(*folders[1:])
            return [], _Destination(str(folder), "script", "script", self.scripts.get(folder, frozenset()))
        if folders[:1] == (WORKFLOWS_TESTS_DIRECTORY,):
            return self._workflow_destination(folders)
        return self._package_destination(folders)

    def _workflow_destination(self, folders: tuple[str, ...]) -> tuple[list[_Problem], _Destination | None]:
        if str(self.directory) != ROOT_DIRECTORY:
            return [(
                "workflow tests belong to the root project's tests/workflows/",
                "move it to the root's tests/workflows/",
            )], None
        problems = []
        if len(folders) > 1:
            problems.append(("workflow tests lie directly in tests/workflows/", "move it up into tests/workflows/"))
        return problems, _Destination(str(checkout.WORKFLOW_DIRECTORY), "workflow", "workflow", self.workflows)

    def _package_destination(self, folders: tuple[str, ...]) -> tuple[list[_Problem], _Destination | None]:
        if len(self.packages) == 1:
            package, below = self.packages[0], folders
        elif folders[:1] and folders[0] in self.packages:
            package, below = folders[0], folders[1:]
        else:
            return [(
                "a project with several packages keeps each test under tests/<package>/",
                "move it under tests/<package>/ of the package it tests",
            )], None
        folder = self.directory / SOURCE_DIRECTORY / package / PurePosixPath(*below)
        if folder not in self.sources:
            return [(
                f"its folder mirrors {folder}, which is no package folder",
                "move it to the folder that mirrors its module's package",
            )], None
        own_name = frozenset() if below else frozenset({_key(package)})
        return [], _Destination(str(folder), "module or package", "module", self.sources[folder] | own_name)


def _folder_keys(files: Sequence[PurePosixPath], root: PurePosixPath) -> dict[PurePosixPath, frozenset[str]]:
    """The key of each file in its folder and of each subfolder in its parent, for every folder below `root`'s
    files."""
    keys: defaultdict[PurePosixPath, set[str]] = defaultdict(set)
    for file in files:
        keys[file.parent].add(_key(file.stem))
        for directory in file.parents:
            if root in directory.parents:
                keys[directory.parent].add(_key(directory.name))
    return {folder: frozenset(names) for folder, names in keys.items()}


def _key(name: str) -> str:
    """`__x__` reads as `x`, otherwise one leading `_` is dropped."""
    wrapped = WRAPPED_NAME.fullmatch(name)
    return wrapped[1] if wrapped else name.removeprefix("_")


def _in_fixtures(folders: Sequence[str]) -> bool:
    return FIXTURES_DIRECTORY in folders


def _word_prefixes(name: str, boundary: re.Pattern[str]) -> list[str]:
    """`name` and each non-empty prefix of it that ends before a word boundary."""
    prefixes = [name[: match.start()] for match in boundary.finditer(name)]
    return [prefix for prefix in prefixes if prefix] + ([name] if name else [])


def _names_a_key(name: str, keys: frozenset[str]) -> bool:
    return not name.startswith("_") and any(prefix in keys for prefix in _word_prefixes(name, PYTHON_WORD_BOUNDARY))


def _mirror_findings(path: PurePosixPath, folders: tuple[str, ...], mirror: _ProjectMirror) -> list[Finding]:
    if _in_fixtures(folders) or folders[:1] == (END_TO_END_DIRECTORY,):
        return []
    if SUPPORT_DIRECTORY in folders:
        return [_mirror_finding(path, [(
            "a test module in a support/ folder, which holds helpers",
            "rename it if it is a helper, or move it to the folder that mirrors its module",
        )])]
    problems, destination = mirror.destination(folders)
    name = path.stem.removeprefix(TEST_PREFIX)
    if destination is not None and not _names_a_key(name, destination.keys):
        named = f"is named by a word prefix of {name}" if name else "is named by an empty name"
        problems.append((
            f"no {destination.noun} in {destination.folder} {named}",
            f"rename it test_<{destination.placeholder}>[_<aspect>].py after what it tests",
        ))
    return [_mirror_finding(path, problems)] if problems else []


def _test_layout_finding(path: str, reason: str, fix: str) -> Finding:
    return Finding(
        Rule.TEST_LAYOUT, path, None, reason,
        f"{fix}, or declare it in {CONFIG_PATH} with [[layout.allow]], its path, the rule test-layout and a reason.",
    )


def _mirror_finding(path: PurePosixPath, problems: Sequence[_Problem]) -> Finding:
    reasons = "; ".join(reason for reason, _ in problems)
    fixes = ", or ".join(fix for _, fix in problems)
    return _test_layout_finding(str(path), f"a test module that does not mirror a module: {reasons}.", fixes)


def _frontend_findings(modes: Mapping[str, str], declared: set[str], python_tests: Iterable[PurePosixPath]) -> list[Finding]:
    """A frontend test sits beside a file it tests and a spec lies in its project's `e2e/`; a path under a judged Python
    `tests/` is left to the Python cases."""
    projects = {PurePosixPath(project) for project in frontend_projects(modes)}
    subjects = _subject_keys(modes)
    findings = []
    for path, mode in modes.items():
        file = PurePosixPath(path)
        project = next((folder for folder in file.parents if folder in projects), None)
        if project is None or path in declared or mode == GITLINK_MODE or not _is_judged_test(file):
            continue
        if any(tests in file.parents for tests in python_tests):
            continue
        findings += _frontend_finding(file, project, subjects.get(file.parent, frozenset()))
    return findings


def _is_judged_test(path: PurePosixPath) -> bool:
    return ratchet.LANGUAGE_OF_SUFFIX.get(path.suffix) in SCRIPT_LANGUAGES and bool(ratchet.runner_test_words(path.name))


def _base(name: str) -> str:
    return name.split(".")[0]


def _subject_keys(modes: Mapping[str, str]) -> dict[PurePosixPath, frozenset[str]]:
    """The key of each file in its folder that is no gitlink and carries no test word."""
    keys: defaultdict[PurePosixPath, set[str]] = defaultdict(set)
    for path, mode in modes.items():
        file = PurePosixPath(path)
        if mode != GITLINK_MODE and not ratchet.runner_test_words(file.name):
            keys[file.parent].add(_base(file.name).removeprefix(ROUTE_MARK))
    return {folder: frozenset(names) for folder, names in keys.items()}


def _frontend_finding(path: PurePosixPath, project: PurePosixPath, keys: frozenset[str]) -> list[Finding]:
    if SPEC_WORD in ratchet.runner_test_words(path.name):
        inside = path.relative_to(project).parts[:1] == (END_TO_END_DIRECTORY,)
        return [] if inside else [_spec_outside_end_to_end(str(path), f"{project / END_TO_END_DIRECTORY}/")]
    base = _base(path.name)
    named = any(prefix in keys for prefix in _word_prefixes(base, FRONTEND_WORD_BOUNDARY))
    return [] if named else [_test_without_sibling(path, base)]


def _spec_outside_end_to_end(path: str, end_to_end: str) -> Finding:
    return _test_layout_finding(
        path,
        f"a spec outside {end_to_end}, where its project keeps its Playwright specs.",
        f"move it into {end_to_end}, or, if it is a Vitest test, name it `<subject>.test.<suffix>` beside its subject",
    )


def _test_without_sibling(path: PurePosixPath, base: str) -> Finding:
    reason = f"no file in {path.parent} is named {base} or a word prefix of it" if base else "its name before the first `.` is empty"
    return _test_layout_finding(
        str(path),
        f"a test that lies beside no file it tests: {reason}.",
        f"move it beside the file it tests and name it `<subject>[<Aspect>]{path.name.removeprefix(base)}`",
    )


def _init_under_tests(path: str) -> Finding:
    return _test_layout_finding(path, "an __init__.py under tests/, which pytest's importlib mode does not need.", "delete it")


def _stray_under_tests(path: str) -> Finding:
    return _test_layout_finding(
        path,
        "a file under tests/ that is no test_*.py or conftest.py and lies in no support/, fixtures/ or top-level "
        "e2e/ folder.",
        "move it to a support/ folder (a helper) or a fixtures/ folder (data)",
    )


class UnparsableTestModule(Exception):
    """A test importer Python cannot decode or parse."""


def test_import_candidates(modes: Mapping[str, str]) -> list[str]:
    """The tracked paths whose imports are judged: every `.py` that is no link under a Python project's `tests/`,
    except inside a `fixtures/` folder. The project need not have a package."""
    tests = _tests_directories(modes)
    return [
        path for path, mode in modes.items()
        if mode not in LINK_MODES and path.endswith(".py") and _in_judged_tests(PurePosixPath(path), tests)
    ]


def check_test_import(
    modes: Mapping[str, str], contents: Mapping[str, bytes], allowances: Sequence[LayoutAllowance]
) -> list[Finding]:
    """No importer imports a test module or a conftest: every `import` and `from ... import` statement is read, in a
    function or under `if TYPE_CHECKING:` too, and an absolute name is red when its components are the trailing
    components of a target's path, a relative one when it resolves to the target's path; a from-import checks its
    module and each imported name. A declaration exempts its importer; a declaration of an untracked path is a finding.
    `contents` holds the bytes of `test_import_candidates`. Findings are sorted by path and line."""
    declared = _declared_paths(allowances, Rule.TEST_IMPORT)
    targets = _ImportTargets.of(modes)
    findings = []
    for path, content in contents.items():
        if path not in declared:
            findings += _import_findings(path, _parsed(path, content), targets)
    findings += [_stale_declaration(path, Rule.TEST_IMPORT) for path in declared - modes.keys()]
    return sorted(findings, key=lambda finding: (finding.path, finding.line or 0))


def _tests_directories(modes: Iterable[str]) -> list[PurePosixPath]:
    return [project.tests_directory for project in python_projects(modes)]


def _in_judged_tests(path: PurePosixPath, tests: Iterable[PurePosixPath]) -> bool:
    return any(
        directory in path.parents and not _in_fixtures(path.relative_to(directory).parts[:-1])
        for directory in tests
    )


@dataclass(frozen=True)
class _ImportTargets:
    """The tracked test modules and conftests: their paths, and every trailing run of components of their paths
    without `.py`, which is how a `pythonpath` entry names them."""

    paths: frozenset[str]
    suffixes: frozenset[tuple[str, ...]]

    @classmethod
    def of(cls, modes: Mapping[str, str]) -> _ImportTargets:
        tests = _tests_directories(modes)
        named = [
            PurePosixPath(path) for path in modes
            if _in_judged_tests(PurePosixPath(path), tests) and _is_import_target(PurePosixPath(path))
        ]
        components = [path.with_suffix("").parts for path in named]
        return cls(
            frozenset(str(path) for path in named),
            frozenset(parts[start:] for parts in components for start in range(len(parts))),
        )

    def absolute(self, name: str) -> bool:
        return tuple(name.split(".")) in self.suffixes

    def relative(self, folder: PurePosixPath, level: int, name: str) -> bool:
        up = level - 1
        if up > len(folder.parts):
            return False
        base = folder.parts[: len(folder.parts) - up]
        return "/".join((*base, *name.split("."))) + ".py" in self.paths


def _is_test_module(path: PurePosixPath) -> bool:
    return path.name.startswith(TEST_PREFIX) and path.suffix == ".py"


def _is_import_target(path: PurePosixPath) -> bool:
    return _is_test_module(path) or (path.name == CONFTEST_FILE and path.suffix == ".py")


def _parsed(path: str, content: bytes) -> ast.Module:
    try:
        return ast.parse(ratchet.python_text(content), filename=path)
    except ratchet.PARSE_ERRORS as error:
        raise UnparsableTestModule(f"{path} is not valid Python ({error})") from error


def _import_findings(path: str, tree: ast.Module, targets: _ImportTargets) -> list[Finding]:
    folder = PurePosixPath(path).parent
    statements = [node for node in ast.walk(tree) if isinstance(node, ast.Import | ast.ImportFrom)]
    named = ((node, _imported_targets(node, folder, targets)) for node in statements)
    return [_imports_a_test_module(path, node.lineno, names) for node, names in named if names]


def _imported_targets(node: ast.Import | ast.ImportFrom, folder: PurePosixPath, targets: _ImportTargets) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names if targets.absolute(alias.name)]
    module = node.module or ""
    names = [module] if module else []
    names += [f"{module}.{alias.name}" if module else alias.name for alias in node.names if alias.name != "*"]
    if node.level:
        dots = "." * node.level
        return [dots + name for name in names if targets.relative(folder, node.level, name)]
    return [name for name in names if targets.absolute(name)]


def _imports_a_test_module(path: str, line: int, names: Sequence[str]) -> Finding:
    return Finding(
        Rule.TEST_IMPORT, path, line,
        f"imports the test module or conftest {', '.join(names)}.",
        "move what it needs to a support/ module, or make it a fixture in conftest.py, or declare it in "
        f"{CONFIG_PATH} with [[layout.allow]], its path, the rule test-import and a reason.",
    )
