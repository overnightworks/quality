import json
import re
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath

from quality import checkout, python_imports, ratchet
from quality.config_allowances import LayoutAllowance, declared_paths
from quality.report import Finding, FindingEvent, Unparsable, allowance_clause, stale_declaration
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
INIT_MODULE = "__init__"
INIT_FILE = f"{INIT_MODULE}.py"
OWN_NAME = ""
CONFTEST_FILE = "conftest.py"
FIXTURES_DIRECTORY = "fixtures"
END_TO_END_DIRECTORY = "e2e"
LIVE_DIRECTORY = "live"
LIVE_FOLDER = PurePosixPath(END_TO_END_DIRECTORY) / LIVE_DIRECTORY
SUPPORT_DIRECTORY = "support"
HELPER_DIRECTORIES = frozenset({SUPPORT_DIRECTORY, FIXTURES_DIRECTORY})
WORKFLOWS_TESTS_DIRECTORY = "workflows"
ROOT_DIRECTORY = "."
ROUTE_MARK = "+"
SCRIPT_LANGUAGES = frozenset({ratchet.Language.TYPESCRIPT, ratchet.Language.JAVASCRIPT})
SPEC_WORD = "spec"
FRONTEND_WORD_BOUNDARY = re.compile(r"[-_]|(?<=[a-z0-9])(?=[A-Z])")
PYTHON_WORD_BOUNDARY = re.compile(r"_")
NOT_MIRRORED_FOLDERS = frozenset({SCRIPTS_DIRECTORY, WORKFLOWS_TESTS_DIRECTORY, END_TO_END_DIRECTORY})
TEST_PREFIX = "test_"
WRAPPED_NAME = re.compile(r"__(.+)__")


def check_file_suffix(modes: Mapping[str, str], allowances: Sequence[LayoutAllowance]) -> list[Finding]:
    """Every tracked file without a suffix is executable, a symlink or gitlink, a dotfile, tool-mandated or declared;
    a declaration of an untracked path is a finding too. `modes` maps each tracked path to its index mode. Findings are
    sorted by path."""
    declared = declared_paths(allowances, Rule.FILE_SUFFIX)
    findings = [_suffixless(path) for path, mode in modes.items() if path not in declared and _lacks_suffix(path, mode)]
    findings += [stale_declaration(path, Rule.FILE_SUFFIX) for path in declared - modes.keys()]
    return sorted(findings, key=lambda finding: finding.path)


def _lacks_suffix(path: str, mode: str) -> bool:
    name = PurePosixPath(path).name
    exempt = mode == EXECUTABLE_MODE or mode in LINK_MODES or name.startswith(".") or name in TOOL_MANDATED_NAMES
    return not exempt and not PurePosixPath(name).suffix


def _suffixless(path: str) -> Finding:
    return Finding(
        Rule.FILE_SUFFIX, path, None,
        "a file without a suffix that is no executable",
        f"give it the suffix of its format, or {allowance_clause(Rule.FILE_SUFFIX)}",
        event=FindingEvent.TRACKED,
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
    problem_of: Callable[[str, bytes], tuple[FindingEvent, str, str] | None],
) -> list[Finding]:
    declared = declared_paths(allowances, rule)
    findings = []
    for path, mode in modes.items():
        content = contents.get(path)
        if content is None or path in declared or not _is_script(path, mode, content):
            continue
        if problem := problem_of(path, content):
            event, fact, fix = problem
            findings.append(Finding(rule, path, None, fact, fix, event=event))
    findings += [stale_declaration(path, rule) for path in declared - modes.keys()]
    return sorted(findings, key=lambda finding: finding.path)


def _under_tests(path: str) -> bool:
    return TESTS_DIRECTORY in PurePosixPath(path).parent.parts


def _is_script(path: str, mode: str, content: bytes) -> bool:
    pure = PurePosixPath(path)
    in_scripts_folder = SCRIPTS_DIRECTORY in pure.parent.parts and pure.suffix in SCRIPT_SUFFIXES
    return mode == EXECUTABLE_MODE or content.startswith(SHEBANG) or in_scripts_folder


def _name_problem(path: str, _content: bytes) -> tuple[FindingEvent, str, str] | None:
    pure = PurePosixPath(path)
    if pure.name.startswith(".") or pure.name in TOOL_MANDATED_NAMES:
        return None
    badly_shaped = not SCRIPT_NAME_SHAPE.fullmatch(pure.stem)
    problems = ["not snake_case with at least two words"] if badly_shaped else []
    if SCRIPTS_DIRECTORY not in pure.parent.parts:
        problems.append(f"outside a {SCRIPTS_DIRECTORY}/ folder")
    if not problems:
        return None
    event = FindingEvent.MISNAMED if badly_shaped else FindingEvent.MISPLACED
    return event, "; ".join(problems), f"rename or move it, or {allowance_clause(Rule.SCRIPT_NAME)}"


def _header_problem(path: str, content: bytes) -> tuple[FindingEvent, str, str] | None:
    markers = ("//", "/*") if PurePosixPath(path).suffix == SLASH_COMMENT_SUFFIX else ("#",)
    if any(_explains(line, markers) for line in _first_lines_after_preamble(content)):
        return None
    return (
        FindingEvent.NO_HEADER,
        f"no header comment or docstring in the first {HEADER_LINES} non-blank lines after its shebang and script metadata",
        f"say what it does in a comment or docstring at the top, or {allowance_clause(Rule.SCRIPT_HEADER)}",
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


def spec_folder(project: PurePosixPath) -> PurePosixPath:
    """The folder of a frontend project's Playwright specs (LAYOUT-51): `tests/e2e/` for the project at the repository
    root, whose `root-layout` accepts no root `e2e/`, and the project's top-level `e2e/` for every other."""
    root = PurePosixPath(ROOT_DIRECTORY)
    return PurePosixPath(TESTS_DIRECTORY, END_TO_END_DIRECTORY) if project == root else project / END_TO_END_DIRECTORY


def package_document(path: str, content: bytes) -> dict:
    """The JSON object a `package.json` holds, empty where it holds another value; a file that is no UTF-8 JSON is `Unparsable`."""
    try:
        document = json.loads(content.decode("utf-8-sig"))
    except (ValueError, RecursionError) as error:
        raise Unparsable(f"{path} is not UTF-8 JSON that Python reads") from error
    return document if isinstance(document, dict) else {}


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
    `test_<module>[_<aspect>].py`, or lies directly beside the `support/` folder that holds a tracked helper it is named
    after (LAYOUT-50); a declaration of an untracked path is a finding too. A project is judged when it
    has a package. A Vitest test of a frontend project lies beside the file it tests and a Playwright spec in the
    project's spec folder (LAYOUT-51)."""
    declared = declared_paths(allowances, Rule.TEST_LAYOUT)
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
    findings += [stale_declaration(path, Rule.TEST_LAYOUT) for path in declared - modes.keys()]
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
    keys: Mapping[str, str]


@dataclass(frozen=True)
class _ProjectMirror:
    """What a project's tests may mirror: the keys of each `src/` package folder and `scripts/` folder, and the
    root's workflows, each key with the name it stands for. `helpers` holds the keys of the tracked helpers directly in
    a `support/` folder, by the folder that holds `support/` (LAYOUT-50)."""

    directory: PurePosixPath
    packages: tuple[str, ...]
    sources: Mapping[PurePosixPath, Mapping[str, str]]
    scripts: Mapping[PurePosixPath, Mapping[str, str]]
    workflows: Mapping[str, str]
    helpers: Mapping[PurePosixPath, Mapping[str, str]]

    @classmethod
    def of(cls, project: PythonProject, tracked: Sequence[PurePosixPath]) -> _ProjectMirror:
        directory = PurePosixPath(project.directory)
        source, scripts = directory / SOURCE_DIRECTORY, directory / SCRIPTS_DIRECTORY
        return cls(
            directory,
            project.packages,
            _folder_keys([path for path in tracked if path.suffix == ".py" and source in path.parents], source),
            _folder_keys([path for path in tracked if scripts in path.parents], scripts),
            {_key(name): name for name in (path.stem.replace("-", "_") for path in tracked if checkout.is_workflow(path))},
            _helper_keys(tracked),
        )

    def names_helper(self, folder: PurePosixPath, test_name: str) -> bool:
        """Whether a word prefix of `test_name` is the key of a tracked helper in the `support/` folder inside `folder`."""
        return _longest_key(test_name, self.helpers.get(folder, {})) is not None

    def subject(self, folders: tuple[str, ...], test_name: str) -> str | None:
        """The module or package the test named `test_<test_name>.py` in `folders` is named after, by its longest key."""
        destination = self.destination(folders)[1]
        key = None if destination is None else _longest_key(test_name, destination.keys)
        if destination is None or key is None:
            return None
        name = destination.keys[key]
        package = PurePosixPath(destination.folder).relative_to(self.directory / SOURCE_DIRECTORY).parts
        return ".".join(package if name in (OWN_NAME, INIT_MODULE) else (*package, name))

    def destination(self, folders: tuple[str, ...]) -> tuple[list[_Problem], _Destination | None]:
        if folders[:1] == (SCRIPTS_DIRECTORY,):
            folder = self.directory / SCRIPTS_DIRECTORY / PurePosixPath(*folders[1:])
            return [], _Destination(str(folder), "script", "script", self.scripts.get(folder, {}))
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
        own_name = {} if below else {_key(package): OWN_NAME}
        return [], _Destination(str(folder), "module or package", "module", {**self.sources[folder], **own_name})


def _folder_keys(files: Sequence[PurePosixPath], root: PurePosixPath) -> dict[PurePosixPath, dict[str, str]]:
    """The key of each file in its folder and of each subfolder in its parent, each with the name it stands for, for
    every folder below `root`'s files."""
    keys: defaultdict[PurePosixPath, dict[str, str]] = defaultdict(dict)
    for file in files:
        keys[file.parent].setdefault(_key(file.stem), file.stem)
        for directory in file.parents:
            if root in directory.parents:
                keys[directory.parent].setdefault(_key(directory.name), directory.name)
    return dict(keys)


def _helper_keys(tracked: Sequence[PurePosixPath]) -> dict[PurePosixPath, dict[str, str]]:
    """The key of each tracked `.py` directly in a `support/` folder, other than `__init__.py` and `conftest.py`, by the
    folder that holds `support/`."""
    keys: defaultdict[PurePosixPath, dict[str, str]] = defaultdict(dict)
    for path in tracked:
        if path.suffix == ".py" and path.name not in (INIT_FILE, CONFTEST_FILE) and path.parent.name == SUPPORT_DIRECTORY:
            keys[path.parent.parent].setdefault(_key(path.stem), path.stem)
    return dict(keys)


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


def _longest_key(name: str, keys: Mapping[str, str]) -> str | None:
    """The longest word prefix of `name` that is a key, None for a name that starts with `_` or names no key."""
    if name.startswith("_"):
        return None
    return max((prefix for prefix in _word_prefixes(name, PYTHON_WORD_BOUNDARY) if prefix in keys), key=len, default=None)


def _mirror_findings(path: PurePosixPath, folders: tuple[str, ...], mirror: _ProjectMirror) -> list[Finding]:
    if _in_fixtures(folders) or folders[:1] == (END_TO_END_DIRECTORY,):
        return []
    if SUPPORT_DIRECTORY in folders:
        return [_mirror_finding(path, [(
            f"a test module in a {SUPPORT_DIRECTORY}/ folder, which holds helpers",
            "rename it if it is a helper, or move it to the folder that mirrors its module",
        )])]
    name = path.stem.removeprefix(TEST_PREFIX)
    if mirror.names_helper(path.parent, name):
        return []
    problems, destination = mirror.destination(folders)
    if destination is not None and _longest_key(name, destination.keys) is None:
        named = f"is named by a word prefix of {name}" if name else "is named by an empty name"
        problems.append((
            f"no {destination.noun} in {destination.folder} {named}",
            f"rename it test_<{destination.placeholder}>[_<aspect>].py after what it tests",
        ))
    return [_mirror_finding(path, problems)] if problems else []


def _test_layout_finding(path: str, event: FindingEvent, fact: str, fix: str) -> Finding:
    return Finding(Rule.TEST_LAYOUT, path, None, fact, f"{fix}, or {allowance_clause(Rule.TEST_LAYOUT)}", event=event)


def _mirror_finding(path: PurePosixPath, problems: Sequence[_Problem]) -> Finding:
    facts = "; ".join(fact for fact, _ in problems)
    fixes = ", or ".join(fix for _, fix in problems)
    return _test_layout_finding(str(path), FindingEvent.MISPLACED, facts, fixes)


def _frontend_findings(modes: Mapping[str, str], declared: set[str], python_tests: Iterable[PurePosixPath]) -> list[Finding]:
    """A frontend test sits beside a file it tests and a spec lies in its project's spec folder; a path under a judged Python
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
        folder = spec_folder(project)
        return [] if folder in path.parents else [_spec_outside_spec_folder(str(path), f"{folder}/")]
    base = _base(path.name)
    named = any(prefix in keys for prefix in _word_prefixes(base, FRONTEND_WORD_BOUNDARY))
    return [] if named else [_test_without_sibling(path, base)]


def _spec_outside_spec_folder(path: str, folder: str) -> Finding:
    return _test_layout_finding(
        path, FindingEvent.MISPLACED,
        f"a Playwright spec outside {folder}",
        f"move it into {folder}, or, if it is a Vitest test, name it `<subject>.test.<suffix>` beside its subject",
    )


def _test_without_sibling(path: PurePosixPath, base: str) -> Finding:
    folder = "the repository root" if str(path.parent) == ROOT_DIRECTORY else str(path.parent)
    fact = f"no file in {folder} is named {base} or a word prefix of it" if base else "its name before the first `.` is empty"
    return _test_layout_finding(
        str(path), FindingEvent.MISPLACED, fact,
        f"move it beside the file it tests and name it `<subject>[<Aspect>]{path.name.removeprefix(base)}`",
    )


def _init_under_tests(path: str) -> Finding:
    return _test_layout_finding(path, FindingEvent.STRAY, f"an {INIT_FILE}, which pytest's importlib mode does not need", "delete it")


def _stray_under_tests(path: str) -> Finding:
    return _test_layout_finding(
        path, FindingEvent.STRAY,
        f"no test_*.py or {CONFTEST_FILE}, in no {SUPPORT_DIRECTORY}/, {FIXTURES_DIRECTORY}/ or top-level {END_TO_END_DIRECTORY}/ folder",
        f"move it to a {SUPPORT_DIRECTORY}/ folder (a helper) or a {FIXTURES_DIRECTORY}/ folder (data)",
    )


def test_subjects(modes: Mapping[str, str], allowances: Sequence[LayoutAllowance]) -> dict[str, str | None]:
    """The module or package each tracked `test_*.py` under a Python project's `tests/` is named after, None where the
    layout rules name none: a `test-layout` declaration exempts the path, the project has no package, or no key of the
    mirrored folder is a word prefix of its name; a helper key (LAYOUT-50) names no subject. A test under the
    `scripts/`, `workflows/` or `e2e/` of its `tests/` has no entry."""
    declared = declared_paths(allowances, Rule.TEST_LAYOUT)
    tracked = [PurePosixPath(path) for path in modes]
    mirrors = {project.tests_directory: _ProjectMirror.of(project, tracked) for project in python_projects(modes) if project.packages}
    subjects: dict[str, str | None] = {}
    for file, tests, folders in _judged_paths(modes):
        if not _is_test_module(file) or (folders and folders[0] in NOT_MIRRORED_FOLDERS):
            continue
        mirror = mirrors.get(tests)
        subjects[str(file)] = None if mirror is None or str(file) in declared else mirror.subject(folders, file.stem.removeprefix(TEST_PREFIX))
    return subjects


class UnparsableTestModule(Exception):
    """A test importer Python cannot decode or parse."""


def test_import_candidates(modes: Mapping[str, str]) -> list[str]:
    """The tracked paths whose imports are judged: every `.py` that is no link under a Python project's `tests/`,
    except inside a `fixtures/` folder. The project need not have a package."""
    return [str(path) for path, _, _ in _judged_paths(modes)]


def check_test_import(
    modes: Mapping[str, str], contents: Mapping[str, bytes], allowances: Sequence[LayoutAllowance]
) -> list[Finding]:
    """No importer imports a test module or a conftest: every `import` and `from ... import` statement is read, in a
    function or under `if TYPE_CHECKING:` too, and an absolute name is red when its components are the trailing
    components of a target's path, a relative one when it resolves to the target's path; a from-import checks its
    module and each imported name. A declaration exempts its importer; a declaration of an untracked path is a finding.
    `contents` holds the bytes of `test_import_candidates`. Findings are sorted by path and line."""
    declared = declared_paths(allowances, Rule.TEST_IMPORT)
    targets = ImportTargets.among(file for file, _, _ in _judged_paths(modes, links=True) if _is_import_target(file))
    findings = []
    for path, content in contents.items():
        if path not in declared:
            findings += _import_findings(path, content, targets)
    findings += [stale_declaration(path, Rule.TEST_IMPORT) for path in declared - modes.keys()]
    return sorted(findings, key=lambda finding: (finding.path, finding.line or 0))


def _judged_paths(modes: Mapping[str, str], *, links: bool = False) -> Iterator[tuple[PurePosixPath, PurePosixPath, tuple[str, ...]]]:
    """Each tracked `.py` under a Python project's `tests/` outside `fixtures/`, with that `tests/` and the folders
    between; a link only with `links`, since an importer is read and a target is only named."""
    for file, directory, folders in _modules_under_tests(modes):
        if links or modes[str(file)] not in LINK_MODES:
            yield file, directory, folders


def _modules_under_tests(paths: Iterable[str]) -> Iterator[tuple[PurePosixPath, PurePosixPath, tuple[str, ...]]]:
    tracked = list(paths)
    tests = [project.tests_directory for project in python_projects(tracked)]
    for path in tracked:
        file = PurePosixPath(path)
        directory = next((directory for directory in tests if directory in file.parents), None)
        if directory is not None and file.suffix == ".py":
            folders = file.relative_to(directory).parts[:-1]
            if not _in_fixtures(folders):
                yield file, directory, folders


def test_project_files(paths: Iterable[str]) -> dict[str, str]:
    """For each tracked `.py` under a Python project's `tests/` outside `fixtures/`, the `pyproject.toml` of that project
    (LAYOUT-1): the one the judged `tests/` belongs to, never the one beside an outer `tests` folder of the path."""
    return {str(file): str(directory.parent / PROJECT_FILE) for file, directory, _ in _modules_under_tests(paths)}


@dataclass(frozen=True)
class ImportTargets:
    """Tracked modules, by their paths and by every trailing run of components of a path without `.py`, which is how a
    `pythonpath` entry names them; an `__init__.py` is also named by its package."""

    paths: frozenset[str]
    by_suffix: Mapping[tuple[str, ...], frozenset[str]]

    @classmethod
    def among(cls, named: Iterable[PurePosixPath]) -> ImportTargets:
        files = list(named)
        suffixes: defaultdict[tuple[str, ...], set[str]] = defaultdict(set)
        for file in files:
            parts = file.with_suffix("").parts
            names = parts[:-1] if parts[-1] == INIT_MODULE else parts
            for start in range(len(names)):
                suffixes[names[start:]].add(str(file))
            for start in range(len(parts)):
                suffixes[parts[start:]].add(str(file))
        return cls(frozenset(str(file) for file in files), {suffix: frozenset(paths) for suffix, paths in suffixes.items()})

    @classmethod
    def support(cls, modes: Mapping[str, str]) -> ImportTargets:
        """The `support/` modules of every judged `tests/`."""
        return cls.among(file for file, _, folders in _judged_paths(modes) if SUPPORT_DIRECTORY in folders)

    def absolute(self, name: str) -> frozenset[str]:
        return self.by_suffix.get(tuple(name.split(".")), frozenset())

    def relative(self, folder: PurePosixPath, level: int, name: str) -> frozenset[str]:
        up = level - 1
        if up > len(folder.parts):
            return frozenset()
        base = folder.parts[: len(folder.parts) - up]
        module = "/".join((*base, *name.split(".")))
        return frozenset({f"{module}.py", f"{module}/{INIT_FILE}"}) & self.paths


def _is_test_module(path: PurePosixPath) -> bool:
    return path.name.startswith(TEST_PREFIX) and path.suffix == ".py"


def _is_import_target(path: PurePosixPath) -> bool:
    return _is_test_module(path) or (path.name == CONFTEST_FILE and path.suffix == ".py")


def _import_findings(path: str, content: bytes, targets: ImportTargets) -> list[Finding]:
    return [_imports_a_test_module(path, statement.line, list(statement.targets)) for statement in module_imports(path, content, targets) if statement.targets]


def _imported_names(statement: python_imports.Statement) -> list[str]:
    """The names a statement imports as it spells them, without the dots of a relative one: a from-import names its
    module and each imported name."""
    if isinstance(statement, python_imports.Import):
        return list(statement.names)
    module = statement.module
    return ([module] if module else []) + [f"{module}.{name}" if module else name for name in statement.names if name != "*"]


def _level(statement: python_imports.Statement) -> int:
    return 0 if isinstance(statement, python_imports.Import) else statement.level


@dataclass(frozen=True)
class ModuleImport:
    """One import statement of a module: its line, the absolute dotted names it imports (none for a relative one) and
    each name, spelled as written, that names a tracked module of the `ImportTargets`, with the paths it names."""

    line: int
    names: tuple[str, ...]
    targets: Mapping[str, frozenset[str]]

    @property
    def modules(self) -> frozenset[str]:
        return frozenset().union(*self.targets.values())


def module_imports(path: str, content: bytes, targets: ImportTargets) -> list[ModuleImport]:
    """Every `import` and `from ... import` statement of the module, in a function or under `if TYPE_CHECKING:` too."""
    try:
        statements = python_imports.import_statements(path, content)
    except ratchet.PARSE_ERRORS as error:
        raise UnparsableTestModule(f"{path} is not valid Python ({error})") from error
    folder = PurePosixPath(path).parent
    imports = []
    for statement in statements:
        level, names = _level(statement), _imported_names(statement)
        named = {"." * level + name: (targets.relative(folder, level, name) if level else targets.absolute(name)) for name in names}
        imports.append(ModuleImport(statement.line, () if level else tuple(names), {name: paths for name, paths in named.items() if paths}))
    return imports


def _imports_a_test_module(path: str, line: int, names: Sequence[str]) -> Finding:
    return Finding(
        Rule.TEST_IMPORT, path, line,
        f"imports {', '.join(names)}",
        f"move what it needs to a {SUPPORT_DIRECTORY}/ module, or make it a fixture in {CONFTEST_FILE}, or "
        f"{allowance_clause(Rule.TEST_IMPORT)}",
        event=FindingEvent.TEST_IMPORT,
    )
