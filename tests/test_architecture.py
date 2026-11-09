"""#61 (Q1b-6a): the import-contract rule judges a repository's import-linter contracts.

Every cell runs through `check_door`; the cell numbers are the item's scenario matrix.
"""

import os
import signal
import subprocess
import sys
from pathlib import Path

import grimp
import pytest
import yaml
from importlinter.contracts.forbidden import ForbiddenContract

import quality
from quality import architecture
from quality.report import FindingEvent, NoteEvent
from quality.rules import Area, Rule
from tests.support.architecture import (
    FORBIDDEN,
    GUARDED_CHANGES,
    SHOP,
    SHOP_DB_IMPORTS_WEB,
    contracts_ini,
    contracts_toml,
    ini_section,
    loosened,
    one,
    toml,
)
from tests.support.check import (
    CLEAN_VERDICT,
    area_fact,
    assert_every_line_starts_with_the_door,
    assert_finding_vocabulary,
    check_result,
    env_row_stands,
    is_clean,
    leading_count,
    refusal_row,
    rule_findings,
    rule_notes,
)
from tests.support.check import check_output as run
from tests.support.merge import BASE_SHA, ONE_JOB, SONAR_GREEN, FakeGh, changed_nothing, declaring, drive

QUALITY_ROOT = Path(__file__).resolve().parents[1]
SHOP_CYCLE = {**SHOP_DB_IMPORTS_WEB, "src/shop/web.py": "import shop.db\n"}
LAYERS = {"type": "layers", "layers": ["shop.web", "shop.db"]}
PROTECTED = {"type": "protected", "protected_modules": ["shop.web"], "allowed_importers": ["shop.api"]}
INDEPENDENCE = {"type": "independence", "modules": ["shop.web", "shop.db"]}
ACYCLIC_SIBLINGS = {"type": "acyclic_siblings", "ancestors": ["shop"]}
STALE_IGNORE = {**FORBIDDEN, "ignore_imports": ["shop.db -> shop.api"]}
REFUSED = f"{refusal_row('imports')}"


def ini(contract: dict, roots: tuple[str, ...] = ("shop",), **session: str | list[str]) -> str:
    """An `.importlinter` or `setup.cfg` with one contract named `guarded`."""
    return ini_section("importlinter", {"root_packages": list(roots), **session}) + ini_section(
        "importlinter:contract:one", {"name": "guarded", **contract}
    )


THREE_ROOTS = {
    "src/alpha/__init__.py": "", "src/alpha/top.py": "import beta.mid\n",
    "libs/beta/__init__.py": "", "libs/beta/mid.py": "import gamma.low\n",
    "tools/gamma/__init__.py": "", "tools/gamma/low.py": "",
}
ALPHA_NEVER_REACHES_GAMMA = {"type": "forbidden", "source_modules": ["alpha"], "forbidden_modules": ["gamma"]}


FINDING_CELLS = [
    ({**SHOP_DB_IMPORTS_WEB, ".importlinter": ini(FORBIDDEN)}, ".importlinter", "guarded", ["shop.db -> shop.web (l.1)"]),
    ({**SHOP_DB_IMPORTS_WEB, "pyproject.toml": toml(LAYERS)}, "pyproject.toml", "guarded", ["shop.db -> shop.web (l.1)"]),
    ({**SHOP_DB_IMPORTS_WEB, "setup.cfg": ini(PROTECTED)}, "setup.cfg", "guarded", ["shop.db"]),
    ({**SHOP_DB_IMPORTS_WEB, ".importlinter": ini(INDEPENDENCE)}, ".importlinter", "guarded", ["shop.db -> shop.web (l.1)"]),
    ({**SHOP_CYCLE, ".importlinter": ini(ACYCLIC_SIBLINGS)}, ".importlinter", "guarded", ["No cycles are allowed in shop."]),
    ({**THREE_ROOTS, ".importlinter": ini(ALPHA_NEVER_REACHES_GAMMA, roots=("alpha", "beta", "gamma"))}, ".importlinter", "guarded",
     ["alpha.top -> beta.mid (l.1)", "beta.mid -> gamma.low (l.1)"]),
    ({**SHOP, ".importlinter": ini(STALE_IGNORE)}, ".importlinter", "shop.db -> shop.api", []),
    ({**SHOP, "src/shop/db.py": "import sqlalchemy\n",
      ".importlinter": ini({**FORBIDDEN, "forbidden_modules": ["sqlalchemy"]}, include_external_packages="True")}, ".importlinter",
     "guarded", ["shop.db -> sqlalchemy (l.1)"]),
]


@pytest.mark.parametrize(
    ("files", "location", "named", "chain"),
    FINDING_CELLS,
    ids=[
        "a broken forbidden contract names the chain",
        "a broken layers contract in pyproject.toml",
        "a broken protected contract in setup.cfg",
        "independence broken",
        "acyclic_siblings broken",
        "a chain across three source roots",
        "a stale ignore_imports entry",
        "an external forbidden module that is not installed",
    ],
)
def test_a_contract_that_does_not_hold_is_one_finding_at_its_config(build_repo, capsys, files, location, named, chain):
    """IMPORT-1, IMPORT-2, IMPORT-6, IMPORT-7, IMPORT-26: a broken contract in any of the three config files, and an
    unmatched ignore at the default `error`, is exit 1 and one finding at the config naming the contract or the entry,
    its chain within the lines import-linter rendered, and no absolute path; the root packages may stand in different
    directories."""
    repo = build_repo(files)
    code, output = run(capsys, repo)
    (finding,) = rule_findings(repo, Rule.IMPORT_CONTRACT)
    assert code == 1, output
    assert finding.location == location
    assert named in finding.fact
    assert [line for line in chain if line not in "\n".join(finding.detail)] == []
    assert str(repo) not in output


@pytest.mark.parametrize(
    ("files", "expected_code", "expected_lines"),
    [
        ({**SHOP, ".importlinter": ini(FORBIDDEN)}, 0, [CLEAN_VERDICT]),
        ({**SHOP, ".importlinter": ini(INDEPENDENCE)}, 0, [CLEAN_VERDICT]),
        ({**SHOP_DB_IMPORTS_WEB, ".importlinter": ini(ACYCLIC_SIBLINGS)}, 0, [CLEAN_VERDICT]),
        ({**SHOP, ".importlinter": ini(FORBIDDEN), "pyproject.toml": toml(FORBIDDEN)}, 2,
         [f"{REFUSED}.importlinter and pyproject.toml each hold an import-linter config; keep one"]),
        ({**SHOP, ".importlinter": ini(FORBIDDEN, roots=("ghost",))}, 2,
         [f"{REFUSED}root package ghost needs exactly one tracked ghost/__init__.py outside the tests, found: none"]),
        ({**SHOP, "lib/shop/__init__.py": "", ".importlinter": ini(FORBIDDEN)}, 2,
         [(f"{REFUSED}root package shop needs exactly one tracked shop/__init__.py outside the tests,"
           " found: lib/shop, src/shop")]),
        ({**SHOP, "tests/shop/__init__.py": "", ".importlinter": ini(FORBIDDEN)}, 0, [CLEAN_VERDICT]),
        ({**SHOP, ".importlinter": ini({"type": "custom"})}, 2,
         [(f"{REFUSED}contract guarded in .importlinter has type custom; only the built-in types are judged"
           " (forbidden, layers, independence, protected, acyclic_siblings)")]),
        ({**SHOP, ".importlinter": ini({"type": "forbidden", "source_modules": ["shop.db"]})}, 2,
         [f"{REFUSED}contract guarded in .importlinter has invalid options (forbidden_modules: This is a required field.)"]),
    ],
    ids=[
        "a kept forbidden contract",
        "independence kept",
        "acyclic_siblings kept",
        "two configs",
        "a root package without a tracked __init__",
        "a root package in two production directories",
        "a root package also under tests",
        "an unknown contract type",
        "a required field missing",
    ],
)
def test_the_import_contract_rule_judges_the_contracts(build_repo, capsys, files, expected_code, expected_lines):
    """IMPORT-1, IMPORT-7, IMPORT-9, IMPORT-11, IMPORT-12, IMPORT-26: kept contracts are clean; a root package needs
    exactly one tracked production directory and a copy under the tests does not count; two configs, a contract type
    that is not built in and invalid contract options are exit 2 naming why; every printed path is
    repository-relative."""
    repo = build_repo(files)
    code, output = run(capsys, repo)
    assert code == expected_code, output
    assert [line for line in expected_lines if line not in output] == [], output
    assert str(repo) not in output


def collision(name: str, directory: str, other_place: str) -> str:
    return (f"{REFUSED}root package {name} is {directory}{name}/__init__.py, but Python finds it {other_place};"
            " judging it would read the wrong package")


@pytest.mark.parametrize(
    ("files", "untracked", "expected"),
    [
        ({"yaml/__init__.py": "", ".importlinter": ini({"type": "independence", "modules": ["yaml.a"]}, roots=("yaml",))},
         None, collision("yaml", "", "outside the repository at site-packages/yaml/__init__.py")),
        ({"src/quality/__init__.py": "",
          ".importlinter": ini({"type": "independence", "modules": ["quality.a"]}, roots=("quality",))},
         None, collision("quality", "src/", "outside the repository at src/quality/__init__.py")),
        ({"src/a/__init__.py": "", "b/__init__.py": "",
          ".importlinter": ini({"type": "independence", "modules": ["a", "b"]}, roots=("a", "b"))},
         "src/b/__init__.py", collision("b", "", "at src/b/__init__.py")),
    ],
    ids=["a root package quality already imported", "quality from a second checkout",
         "an untracked copy inside the repository found first"],
)
def test_a_root_package_python_finds_elsewhere_names_both_places_without_a_local_path(
        build_repo, capsys, files, untracked, expected):
    """IMPORT-15, IMPORT-16, IMPORT-26: a root package Python finds in another place is exit 2 naming the tracked place
    and the other place repository-relative, the latter from its sys.path entry when it lies outside, never by a local
    path."""
    repo = build_repo(files)
    if untracked:
        (repo / untracked).parent.mkdir(parents=True, exist_ok=True)
        (repo / untracked).write_text("")
    code, output = run(capsys, repo)
    assert code == 2, output
    assert expected in output, output
    local_places = (str(Path(os.path.realpath(module.__file__)).parent.parent) for module in (yaml, quality))
    assert [place for place in (*local_places, str(repo), "../") if place in output] == [], output


KIND_CELLS = [
    pytest.param({**SHOP_DB_IMPORTS_WEB, ".importlinter": ini(FORBIDDEN)}, [FindingEvent.BROKEN], [], id="a broken contract"),
    pytest.param({**SHOP, ".importlinter": ini(STALE_IGNORE)}, [FindingEvent.UNUSED], [], id="an unmatched ignore at error"),
    pytest.param({**SHOP, ".importlinter": ini({**STALE_IGNORE, "unmatched_ignore_imports_alerting": "warn"})}, [],
                 [NoteEvent.UNUSED], id="an unmatched ignore at warn"),
    pytest.param({**SHOP, ".importlinter": ini({**STALE_IGNORE, "unmatched_ignore_imports_alerting": "none"})}, [], [],
                 id="an unmatched ignore at none"),
    pytest.param({**SHOP, "src/shop/some.module.py": "", ".importlinter": ini(FORBIDDEN)}, [], [NoteEvent.WARNING],
                 id="a grimp warning"),
]


@pytest.mark.parametrize(("files", "finding_events", "note_events"), KIND_CELLS)
def test_each_kind_of_import_contract_result_carries_its_event_at_the_config(
    build_repo, files, finding_events, note_events
):
    """O23, IMPORT-1, IMPORT-6, ROW-7, ROW-14: a broken contract and an unmatched ignore at `error` are findings of
    their own event, one at `warn` or a grimp warning is a note of its own, `none` is nothing; a finding's location is
    the config, and its fact and fix carry no rule id and no full stop."""
    repo = build_repo(files)
    findings, notes = rule_findings(repo, Rule.IMPORT_CONTRACT), rule_notes(repo, Rule.IMPORT_CONTRACT)
    assert ([finding.event for finding in findings], [note.event for note in notes]) == (finding_events, note_events)
    assert {row.location for row in (*findings, *notes)} <= {".importlinter"}
    for finding in findings:
        assert_finding_vocabulary(finding)


def test_an_unmatched_ignore_names_its_expression_and_a_grimp_warning_the_skipped_module_without_a_local_path(build_repo):
    """IMPORT-6, IMPORT-26: an unmatched ignore at `warn` is a note naming the ignored expression, and a grimp warning
    names the skipped module repository-relative."""
    ignoring = build_repo({**SHOP, ".importlinter": ini({**STALE_IGNORE, "unmatched_ignore_imports_alerting": "warn"})})
    (unused,) = rule_notes(ignoring, Rule.IMPORT_CONTRACT)
    skipping = build_repo({**SHOP, "src/shop/some.module.py": "", ".importlinter": ini(FORBIDDEN)})
    (warning,) = rule_notes(skipping, Rule.IMPORT_CONTRACT)
    assert "shop.db -> shop.api" in unused.fact
    assert ("src/shop/some.module.py" in warning.fact, str(skipping) in warning.fact) == (True, False)


@pytest.mark.parametrize(("files", "event", "remedy"), [
    ({**SHOP, ".importlinter": ini(STALE_IGNORE)}, FindingEvent.UNUSED, (".importlinter", ini(FORBIDDEN))),
    ({**SHOP_DB_IMPORTS_WEB, ".importlinter": ini(FORBIDDEN)}, FindingEvent.BROKEN, ("src/shop/db.py", "x = 1\n")),
], ids=["an unmatched ignore", "an offending import"])
def test_removing_the_unmatched_ignore_or_the_offending_import_clears_its_finding(build_repo, files, event, remedy):
    """O23, IMPORT-1, IMPORT-6: the finding is there first; removing the unmatched ignore, or the offending import,
    leaves the rule clean."""
    repo = build_repo(files)
    before = [finding.event for finding in rule_findings(repo, Rule.IMPORT_CONTRACT)]
    (repo / remedy[0]).write_text(remedy[1])
    assert (before, rule_findings(repo, Rule.IMPORT_CONTRACT)) == ([event], [])


NO_CONTRACTS = {"root_packages": ["shop"]}
TWO_KEPT = {"guarded": FORBIDDEN, "apart": {"type": "independence", "modules": ["shop.web", "shop.api"]}}


@pytest.mark.parametrize(
    "configs",
    [
        {".importlinter": ini_section("importlinter", NO_CONTRACTS)},
        {".importlinter": contracts_ini({"guarded": FORBIDDEN})},
        {".importlinter": contracts_ini(TWO_KEPT)},
        {"setup.cfg": contracts_ini(TWO_KEPT)},
        {"pyproject.toml": contracts_toml({})},
        {"pyproject.toml": contracts_toml(TWO_KEPT)},
    ],
    ids=["no contract", "one contract", "two contracts", "setup.cfg", "pyproject.toml without a contract", "pyproject.toml"],
)
def test_the_imports_row_counts_the_contracts_the_config_declares_and_the_judgement_stays_clean(build_repo, capsys, configs):
    """IMPORT-1, IMPORT-2, ROW-11: each config file declares contracts, the judgement stays clean and the row counts
    the contracts the config declares, `0 contracts` for a config without one."""
    repo = build_repo({**SHOP, **configs})
    _, output = run(capsys, repo)
    (verdict,) = [found for found in check_result(repo).verdicts if found.rule is Rule.IMPORT_CONTRACT]
    declared = len(architecture.contracts_of(configs).contracts)
    assert (verdict.findings, verdict.measured_only) == ((), False)
    assert (verdict.extent, leading_count(area_fact(output, Area.IMPORTS))) == (declared, declared)


def test_a_dotted_root_package_never_runs_its_parent(build_repo, tmp_path, capsys, monkeypatch):
    """IMPORT-8, IMPORT-14: finding a dotted root package would import its parent, so the rule refuses it and the
    parent's code never runs."""
    marker = tmp_path.parent / f"{tmp_path.name}-parent-ran"
    repo = build_repo({**SHOP, "src/shop/__init__.py": f"open({str(marker)!r}, 'w').close()\n",
                       ".importlinter": ini(INDEPENDENCE, roots=("shop.web",))})
    monkeypatch.syspath_prepend(str(repo / "src"))
    code, output = run(capsys, repo)
    assert code == 2, output
    assert f"{REFUSED}root package shop.web is dotted; finding it would import shop" in output
    assert not marker.exists()


def test_a_plugin_contract_type_is_never_imported(build_repo, tmp_path, capsys, monkeypatch):
    """IMPORT-8, IMPORT-10: a `contract_types` session option names modules import-linter would import, so the rule
    refuses it and the plugin's code never runs."""
    marker = tmp_path.parent / f"{tmp_path.name}-plugin-ran"
    repo = build_repo({**SHOP, "plugin.py": f"open({str(marker)!r}, 'w').close()\n",
                       ".importlinter": ini(INDEPENDENCE, contract_types=["custom: plugin.Custom"])})
    monkeypatch.syspath_prepend(str(repo))
    code, output = run(capsys, repo)
    assert code == 2, output
    assert f"{REFUSED}.importlinter sets contract_types, whose plugin modules would be imported" in output
    assert not marker.exists()


def test_a_syntax_error_is_unparsable_and_hides_no_other_rule(build_repo, capsys):
    """IMPORT-24, IMPORT-26, REFUSAL-3: a module grimp cannot parse is exit 2 with its reason, naming no absolute path;
    the env-file rule still judges."""
    repo = build_repo({**SHOP, "src/shop/bad.py": "def broken(:\n", ".env": "A=1\n", ".importlinter": ini(FORBIDDEN)})
    code, output = run(capsys, repo)
    assert code == 2, output
    assert f"{refusal_row('imports')}Syntax error in src/shop/bad.py, line 1" in output
    assert env_row_stands(output)
    assert str(repo) not in output


def test_a_config_linked_out_of_the_repository_cannot_be_judged(build_repo, elsewhere, capsys):
    """IMPORT-8, IMPORT-26: a config that is a link out of the repository cannot be judged, and the row never names the
    outside place."""
    repo = build_repo({**SHOP, ".importlinter": ini(FORBIDDEN)})
    (elsewhere / "outside.ini").write_text(ini(FORBIDDEN))
    (repo / ".importlinter").unlink()
    (repo / ".importlinter").symlink_to(elsewhere / "outside.ini")
    code, output = run(capsys, repo)
    assert code == 2, output
    assert f"{REFUSED}.importlinter resolves outside the repository" in output
    assert str(elsewhere) not in output


@pytest.mark.parametrize(
    ("link", "target", "refused"),
    [("src/shop/ext", "{elsewhere}", "src/shop/ext"),
     ("src/shop/ext", "../../vendor", "src/shop/ext/out")],
    ids=["a direct link", "a chain through a link inside the repository"],
)
def test_a_symlink_out_of_a_root_package_is_refused_before_grimp_reads(build_repo, elsewhere, capsys, monkeypatch,
                                                                       link, target, refused):
    """IMPORT-8, IMPORT-17, IMPORT-26: grimp follows symlinks, so a link leaving the repository, directly or through a
    link that stays inside, is refused and no graph is built; the outside module would break the contract if read, and
    the outside place is never named."""
    (elsewhere / "__init__.py").write_text("")
    (elsewhere / "leak.py").write_text("import shop.web\n")
    repo = build_repo({**SHOP, "vendor/__init__.py": "",
                       ".importlinter": ini({**FORBIDDEN, "source_modules": ["shop.ext"]})})
    (repo / "vendor" / "out").symlink_to(elsewhere)
    (repo / link).symlink_to(target.format(elsewhere=elsewhere))
    graphs_built = []
    monkeypatch.setattr(grimp, "build_graph", lambda *roots, **options: graphs_built.append(roots))
    code, output = run(capsys, repo)
    assert code == 2, output
    assert f"{REFUSED}{refused} links outside the repository" in output
    assert (graphs_built, "leak" in output, str(elsewhere) in output) == ([], False, False)


@pytest.mark.parametrize(
    ("link", "target", "expected"),
    [("again", ".", "src/shop/again links back into src/shop"),
     ("loop.py", "loop.py", "src/shop/loop.py links back into src/shop/loop.py")],
    ids=["a directory loop", "a file loop"],
)
def test_a_symlink_loop_under_a_root_package_cannot_be_judged(build_repo, capsys, link, target, expected):
    """IMPORT-18, IMPORT-26: grimp would walk a symlink loop without end, so a loop of directories or of one file is
    refused, named repository-relative."""
    repo = build_repo({**SHOP, ".importlinter": ini(FORBIDDEN)})
    (repo / "src" / "shop" / link).symlink_to(target)
    code, output = run(capsys, repo)
    assert code == 2, output
    assert f"{REFUSED}{expected}; a symlink loop cannot be judged" in output
    assert "OSError" not in output


def test_a_file_link_into_a_loop_outside_the_repository_is_refused_as_leaving_it(build_repo, elsewhere, capsys):
    """IMPORT-17, IMPORT-26: a link that leaves the repository is named as such, repository-relative, even where the
    place it reaches loops."""
    repo = build_repo({**SHOP, ".importlinter": ini(FORBIDDEN)})
    (elsewhere / "p").symlink_to("q")
    (elsewhere / "q").symlink_to("p")
    (repo / "src" / "shop" / "x.py").symlink_to(elsewhere / "p")
    code, output = run(capsys, repo)
    assert code == 2, output
    assert f"{REFUSED}src/shop/x.py links outside the repository" in output
    assert "../" not in output


def test_a_dangling_file_link_under_a_root_package_is_named_not_blamed_on_the_config(build_repo, capsys):
    """IMPORT-19, IMPORT-26: a dangling link is named as such, repository-relative; no message blames the config for
    a file."""
    repo = build_repo({**SHOP, ".importlinter": ini(FORBIDDEN)})
    (repo / "src" / "shop" / "dangling.py").symlink_to("missing.py")
    code, output = run(capsys, repo)
    assert code == 2, output
    assert f"{REFUSED}src/shop/dangling.py links to nothing; a dangling link cannot be judged" in output
    assert "the config" not in output


def _unreadable_file(root):
    (root / "sealed").mkdir()
    (root / "sealed" / "inner.py").write_text("")
    return root / "sealed", 0o444, "src/shop/sealed/inner.py"


def _unreadable_python_file(root):
    (root / "locked.py").write_text("")
    return root / "locked.py", 0o000, "src/shop/locked.py"


def _unreadable_directory_with_a_file(root):
    (root / "data").mkdir()
    (root / "data" / "f.txt").write_text("")
    return root / "data", 0o000, "src/shop/data"


def _empty_unreadable_directory(root):
    (root / "data").mkdir()
    return root / "data", 0o000, "src/shop/data"


def assert_no_native_or_local_noise(output: str, repo: Path) -> None:
    """IMPORT-26: no repository path, Rust panic text or config blame on either descriptor."""
    for noise in (str(repo), "panicked", "RUST_BACKTRACE", "the config", "PermissionError"):
        assert noise not in output


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a 0o444 directory, so nothing blocks the read")
@pytest.mark.parametrize("arrange", [_unreadable_file, _unreadable_python_file, _unreadable_directory_with_a_file,
                                     _empty_unreadable_directory])
def test_a_place_that_cannot_be_examined_is_cannot_judge_naming_it(build_repo, capfd, arrange):
    """IMPORT-23, IMPORT-26: a read failure other than a loop or a dangling link, of a .py file or of a directory, is
    exit 2 naming the place repository-relative, not the config."""
    repo = build_repo({**SHOP, ".importlinter": ini(FORBIDDEN)})
    sealed, mode, named = arrange(repo / "src" / "shop")
    sealed.chmod(mode)
    try:
        code, output = run(capfd, repo)
    finally:
        sealed.chmod(0o755)
    assert code == 2, output
    assert f"{REFUSED}{named} cannot be examined: Permission denied" in output
    assert_no_native_or_local_noise(output, repo)


@pytest.fixture
def bounded():
    """A walk that opens a FIFO blocks forever, so the test fails after a few seconds instead of hanging."""
    def give_up(*_):
        raise TimeoutError("the walk opened a file it should have refused")
    previous = signal.signal(signal.SIGALRM, give_up)
    signal.alarm(10)
    yield
    signal.alarm(0)
    signal.signal(signal.SIGALRM, previous)


def _fifo_named_py(package):
    os.mkfifo(package / "pipe.py")
    return "src/shop/pipe.py", "not a regular file"


def _non_utf8_content(package):
    (package / "latin.py").write_bytes(b"\xff\xfe")
    return "src/shop/latin.py", "not valid UTF-8"


def _unknown_coding_cookie(package):
    (package / "cookie.py").write_text("# coding: nonsense-xx\nx = 1\n")
    return "src/shop/cookie.py", "unknown encoding: nonsense-xx"


def _latin1_coding_cookie(package):
    (package / "cookie.py").write_text("# coding: latin-1\nx = 1\n")
    return "src/shop/cookie.py", "declares latin-1; grimp reads only"


def _unknown_coding_cookie_in_a_source_with_a_nul(package):
    (package / "cookie.py").write_bytes(b"# coding: nonsense-xx\nx = 1\x00\n")
    return "src/shop/cookie.py", "unknown encoding: nonsense-xx"


def _coding_cookie_after_code(package):
    (package / "cookie.py").write_text("x = 1\n# coding: latin-1\nimport a\n")
    return "src/shop/cookie.py", "declares latin-1; grimp reads only"


def _unknown_coding_cookie_after_a_nul(package):
    (package / "cookie.py").write_bytes(b"\x00\n# coding: bogus\n")
    return "src/shop/cookie.py", "declares bogus; grimp reads only"


def _bom_conflicting_with_a_coding_cookie(package):
    (package / "cookie.py").write_bytes(b"\xef\xbb\xbf# coding: latin-1\nx = 1\n")
    return "src/shop/cookie.py", "encoding problem: utf-8"


def _python_alias_of_utf8(spelling):
    def arrange(package):
        (package / "cookie.py").write_text(f"# coding: {spelling}\nx = 1\n")
        return "src/shop/cookie.py", f"declares {spelling}; grimp reads only"
    return arrange


def _non_utf8_file_name(package):
    try:
        (package / os.fsdecode(b"\xffz.py")).write_text("")
    except OSError:
        pytest.skip("this filesystem refuses a file name that is not UTF-8")
    return "src/shop/\\udcffz.py", "file name is not valid UTF-8"


@pytest.mark.parametrize("arrange", [_fifo_named_py, _non_utf8_content, _unknown_coding_cookie,
                                     _latin1_coding_cookie, _non_utf8_file_name,
                                     _unknown_coding_cookie_in_a_source_with_a_nul,
                                     _bom_conflicting_with_a_coding_cookie, _coding_cookie_after_code,
                                     _unknown_coding_cookie_after_a_nul,
                                     *map(_python_alias_of_utf8, ["utf_8", "UTF_8", "utf-8-sig", "U8", "cp65001",
                                                                  "UTF-8-unix"])])
def test_a_source_grimp_cannot_take_is_refused_before_grimp_can_panic(build_repo, capfd, bounded, arrange):
    """IMPORT-20, IMPORT-21, IMPORT-22, IMPORT-26: a .py file that is no regular file, has a name or content grimp
    cannot decode, or declares an encoding other than UTF-8 is exit 2 naming it, with no Rust panic text, no absolute
    path, no hang."""
    repo = build_repo({**SHOP, ".importlinter": ini(FORBIDDEN)})
    named, reason = arrange(repo / "src" / "shop")
    code, output = run(capfd, repo)
    assert code == 2, output
    assert f"{REFUSED}{named} cannot be examined: {reason}" in output
    assert_no_native_or_local_noise(output, repo)


@pytest.mark.parametrize("cookie_line, import_line", [(f"# coding: {spelling}\n", 2) for spelling in
                                                      ["utf-8", "utf8", "UTF8", "Utf-8", "ascii", "US-ASCII"]]
                         + [("x = 1\n# coding: utf-8\n", 3)])
def test_a_coding_cookie_grimp_reads_is_judged_normally(build_repo, capfd, cookie_line, import_line):
    """IMPORT-22: a cookie spelling grimp accepts, in any letter case, does not stop the judgement of the forbidden
    import."""
    repo = build_repo({**SHOP_DB_IMPORTS_WEB, "src/shop/db.py": f"{cookie_line}import shop.web\n",
                       ".importlinter": ini(FORBIDDEN)})
    code, output = run(capfd, repo)
    assert code == 1, output
    assert f"shop.db -> shop.web (l.{import_line})" in output


@pytest.mark.parametrize("source, echoed", [("x = 1\0y = 2\n", "x = 1\\x00y = 2"),
                                            ("x = 1 +" + "+" * 400 + "(\n", "…")])
def test_the_source_text_a_syntax_error_echoes_is_escaped_and_cut(build_repo, capfd, source, echoed):
    """IMPORT-24, MASK-3, MASK-6: the reason of a module grimp cannot parse echoes its source text: a control character
    is escaped and a long line is cut."""
    repo = build_repo({**SHOP, "src/shop/bad.py": source, ".importlinter": ini(FORBIDDEN)})
    code, output = run(capfd, repo)
    assert code == 2, output
    assert echoed in output
    assert_every_line_starts_with_the_door(output.split("\n"))
    assert all(len(line) < 400 for line in output.splitlines())


def test_a_config_linked_to_nowhere_cannot_be_judged(build_repo, capsys):
    """IMPORT-4: a config that is a link to nowhere is a name the tree holds and the rule cannot read: exit 2."""
    repo = build_repo({**SHOP, ".importlinter": ini(FORBIDDEN)})
    (repo / ".importlinter").unlink()
    (repo / ".importlinter").symlink_to("missing.ini")
    code, output = run(capsys, repo)
    assert (code, f"{REFUSED}cannot read .importlinter: it leads to nothing" in output) == (2, True), output


def test_a_tracked_package_init_deleted_from_disk_cannot_be_judged_without_a_traceback(build_repo, capsys):
    """A tracked __init__.py of a root package that is gone from disk is exit 2, never a traceback."""
    repo = build_repo({**SHOP_DB_IMPORTS_WEB, ".importlinter": ini(FORBIDDEN)})
    (repo / "src/shop/__init__.py").unlink()
    code, output = run(capsys, repo)
    assert (code, REFUSED in output, "Traceback" in output) == (2, True, False), output


def test_an_error_inside_a_contract_check_names_the_contract_without_a_traceback(build_repo, capsys, monkeypatch):
    """IMPORT-13: any other error import-linter or grimp raises is exit 2 naming the contract, never a traceback."""
    def raising_check(contract, graph, verbose):
        raise RuntimeError("boom")

    monkeypatch.setattr(ForbiddenContract, "check", raising_check)
    code, output = run(capsys, build_repo({**SHOP, ".importlinter": ini(FORBIDDEN)}))
    assert code == 2, output
    assert f"{REFUSED}contract guarded in .importlinter cannot be judged: RuntimeError: boom" in output
    assert "Traceback" not in output


@pytest.mark.parametrize(
    ("config", "subject"),
    [(toml({"type": ["forbidden"]}), "contract guarded in pyproject.toml"),
     (toml({**FORBIDDEN, "as_packages": 1}), "contract guarded in pyproject.toml"),
     (toml(FORBIDDEN, roots=(1,)), "the config pyproject.toml")],
    ids=["a type that is a list", "a boolean option that is a number", "a root package that is a number"],
)
def test_a_malformed_config_value_cannot_be_judged_without_a_traceback(build_repo, capsys, config, subject):
    """IMPORT-13: a config value of the wrong type is exit 2 naming the contract or the config, never a traceback."""
    code, output = run(capsys, build_repo({**SHOP, "pyproject.toml": config}))
    assert code == 2, output
    assert f"{REFUSED}{subject} cannot be judged: " in output
    assert "Traceback" not in output


@pytest.mark.parametrize("files", [SHOP, SHOP_DB_IMPORTS_WEB], ids=["kept", "broken"])
def test_a_run_writes_nothing_and_restores_sys_path(build_repo, capsys, files):
    """IMPORT-5: a run, kept or broken, writes no cache, changes nothing in the checkout and leaves sys.path
    as it was."""
    repo = build_repo({**files, ".importlinter": ini(FORBIDDEN)})
    path_before = list(sys.path)
    run(capsys, repo)
    status = subprocess.run(["git", "-C", str(repo), "status", "--porcelain", "--ignored"],
                            capture_output=True, text=True, check=True)
    assert (status.stdout, (repo / ".import_linter_cache").exists(), sys.path) == ("", False, path_before)


@pytest.mark.integration
def test_qualitys_own_tree_holds_its_layers(capsys):
    """IMPORT-1, ROW-11: quality's own layers contract is kept, and the clean row counts its contracts."""
    code, output = run(capsys, QUALITY_ROOT)
    declared = architecture.contracts_of({"pyproject.toml": (QUALITY_ROOT / "pyproject.toml").read_text()}).contracts
    assert (code, is_clean(output), leading_count(area_fact(output, Area.IMPORTS))) == (0, True, len(declared)), output


IGNORING = {**FORBIDDEN, "ignore_imports": ["shop.db -> shop.web"]}
ALLOWING_INDIRECT = {**FORBIDDEN, "allow_indirect_imports": True}
PLAIN_FILE = "[metadata]\nname = shop\n"


@pytest.mark.parametrize(
    ("base", "head", "expected"),
    [
        ({".importlinter": contracts_ini({"a": FORBIDDEN, "b": FORBIDDEN})},
         {".importlinter": contracts_ini({"a": FORBIDDEN})}, ['import contract "b" removed']),
        ({"pyproject.toml": contracts_toml({"a": FORBIDDEN, "b": FORBIDDEN})},
         {"pyproject.toml": contracts_toml({"b": FORBIDDEN})}, ['import contract "a" removed']),
        ({".importlinter": contracts_ini({"a": FORBIDDEN, "b": FORBIDDEN})}, {},
         ["import-linter config in .importlinter removed"]),
        ({"setup.cfg": contracts_ini({"a": FORBIDDEN})}, {"setup.cfg": PLAIN_FILE},
         ["import-linter config in setup.cfg removed"]),
        ({"pyproject.toml": contracts_toml({"a": FORBIDDEN})}, {"pyproject.toml": "[project]\nname = 'x'\n"},
         ["import-linter config in pyproject.toml removed"]),
        ({".importlinter": contracts_ini({"a": FORBIDDEN, "b": FORBIDDEN})}, {".importlinter": contracts_ini({})},
         ['import contract "a" removed', 'import contract "b" removed']),
        ({"pyproject.toml": contracts_toml(one(FORBIDDEN))}, {"pyproject.toml": contracts_toml(one(IGNORING))},
         ['import contract "guarded" ignores shop.db -> shop.web']),
        ({"pyproject.toml": contracts_toml(one(FORBIDDEN))},
         {"pyproject.toml": contracts_toml(one({**FORBIDDEN, "ignore_imports": ["shop.db -> shop.web", "shop.api->shop.*"]}))},
         ['import contract "guarded" ignores shop.api -> shop.*', 'import contract "guarded" ignores shop.db -> shop.web']),
        ({".importlinter": contracts_ini(one(IGNORING))},
         {".importlinter": contracts_ini(one({**IGNORING, "ignore_imports": ["shop.db -> shop.web", "shop.db -> shop.*"]}))},
         ['import contract "guarded" ignores shop.db -> shop.*']),
        ({".importlinter": contracts_ini(one(IGNORING))},
         {".importlinter": contracts_ini(one({**FORBIDDEN, "ignore_imports": ["shop.db->shop.web"]}))}, []),
        ({"pyproject.toml": contracts_toml(one(IGNORING))}, {"pyproject.toml": contracts_toml(one(FORBIDDEN))}, []),
        ({".importlinter": contracts_ini(one(FORBIDDEN))}, {".importlinter": contracts_ini(one(ALLOWING_INDIRECT))},
         [GUARDED_CHANGES + "allow_indirect_imports"]),
        ({".importlinter": contracts_ini(one(FORBIDDEN))},
         {".importlinter": contracts_ini(one({**FORBIDDEN, "unmatched_ignore_imports_alerting": "none"}))},
         [GUARDED_CHANGES + "unmatched_ignore_imports_alerting"]),
        ({".importlinter": contracts_ini(one(FORBIDDEN))}, {".importlinter": contracts_ini(one(LAYERS))},
         [GUARDED_CHANGES + "type"]),
        ({"pyproject.toml": contracts_toml(one({**FORBIDDEN, "source_modules": ["shop.db", "shop.api"]}))},
         {"pyproject.toml": contracts_toml(one(FORBIDDEN))}, [GUARDED_CHANGES + "source_modules"]),
        ({"pyproject.toml": contracts_toml(one(LAYERS))},
         {"pyproject.toml": contracts_toml(one({"type": "layers", "layers": ["shop.db", "shop.web"]}))},
         [GUARDED_CHANGES + "layers"]),
        ({".importlinter": contracts_ini(one(FORBIDDEN))}, {".importlinter": contracts_ini(one(FORBIDDEN), roots=("shop", "other"))},
         ["import-linter changes root_packages"]),
        ({".importlinter": contracts_ini(one(FORBIDDEN), include_external_packages="False")},
         {".importlinter": contracts_ini(one(FORBIDDEN), include_external_packages="True")},
         ["import-linter changes include_external_packages"]),
        ({".importlinter": contracts_ini(one(FORBIDDEN))},
         {".importlinter": contracts_ini(one(FORBIDDEN), exclude_type_checking_imports="True")},
         ["import-linter changes exclude_type_checking_imports"]),
        ({".importlinter": contracts_ini(one(FORBIDDEN), roots=("shop", "other"))},
         {".importlinter": contracts_ini(one(FORBIDDEN), roots=("other", "shop", "shop"))}, []),
        ({".importlinter": contracts_ini(one(IGNORING))},
         {".importlinter": contracts_ini({"guarded": FORBIDDEN, "fresh": LAYERS})}, []),
        ({".importlinter": contracts_ini(one({**FORBIDDEN, "broken_contract_guidance": "ask the web team"}))},
         {".importlinter": contracts_ini(one({**FORBIDDEN, "broken_contract_guidance": "ask the db team"}))}, []),
        ({".importlinter": contracts_ini(one({**ALLOWING_INDIRECT, "as_packages": True}))},
         {"pyproject.toml": contracts_toml(one({**ALLOWING_INDIRECT, "as_packages": True}))}, []),
        ({"setup.cfg": contracts_ini(one(IGNORING), include_external_packages="True")},
         {".importlinter": contracts_ini(one(IGNORING), include_external_packages="True")}, []),
        ({".importlinter": contracts_ini({"old": FORBIDDEN})}, {".importlinter": contracts_ini({"new": FORBIDDEN})},
         ['import contract "old" removed']),
        ({}, {".importlinter": contracts_ini(one(FORBIDDEN))}, []),
        ({}, {}, []),
        ({".importlinter": contracts_ini({"a": FORBIDDEN, "b": LAYERS})},
         {".importlinter": contracts_ini({"a": {**ALLOWING_INDIRECT, "ignore_imports": ["shop.db -> shop.api"]}}, roots=("shop", "x"))},
         ['import contract "a" changes allow_indirect_imports', 'import contract "a" ignores shop.db -> shop.api',
          'import contract "b" removed', "import-linter changes root_packages"]),
    ],
    ids=[
        "cell 1 a removed contract in INI", "cell 2 a removed contract in TOML", "cell 5 the file deleted is one line",
        "cell 6 setup.cfg loses its section", "cell 6 pyproject.toml loses its table", "cell 23 no contract is left",
        "cell 8 a new ignore", "cell 8 two new ignores", "cell 9 a wildcard is a new expression",
        "cell 10 other spacing around the arrow", "cell 16 a removed ignore", "cell 12 allow_indirect_imports",
        "cell 13 alerting", "cell 13 type", "cell 14 a module off source_modules", "cell 14 layers in another order",
        "cell 15 root_packages", "cell 15 include_external_packages", "cell 15 exclude_type_checking_imports",
        "cell 15 root order and duplicates", "cell 16 a new contract and a removed ignore", "cell 17 reworded guidance",
        "cell 18 INI to TOML", "cell 19 setup.cfg to .importlinter", "cell 20 a rename", "cell 22 adoption",
        "no config at either side", "cell 27 several changes, one line each, sorted",
    ],
)
def test_the_contract_diff_names_each_loosening_on_its_own_line(base, head, expected):
    """#148 C1-C7, I1, I2, I5, S1, S2, X1: the texts the verdict judges as the import contract's loosenings."""
    assert loosened(base, head) == expected


@pytest.mark.parametrize(
    ("files", "message"),
    [
        ({".importlinter": contracts_ini({"a": FORBIDDEN}) + ini_section("importlinter:contract:two", {"name": "a", **FORBIDDEN})},
         'import contract "a" is declared twice in .importlinter'),
        ({"pyproject.toml": contracts_toml({"a": FORBIDDEN}) + contracts_toml({"a": FORBIDDEN}).split("\n\n", 1)[1]},
         'import contract "a" is declared twice in pyproject.toml'),
        ({".importlinter": contracts_ini({"a": FORBIDDEN}), "setup.cfg": contracts_ini({"b": FORBIDDEN})},
         ".importlinter and setup.cfg each hold an import-linter config; keep one"),
        ({"pyproject.toml": contracts_toml({"a": FORBIDDEN}, roots=(["shop"],))},
         "the config pyproject.toml cannot be judged: "),
    ],
    ids=["cell 21 a duplicate name in INI", "cell 21 a duplicate name in TOML", "cell 25 two configs", "cell 25 a root package that is a list"],
)
def test_a_side_that_names_a_contract_twice_or_holds_two_configs_cannot_be_judged(files, message):
    """#148 I4, S3, S4: a side whose contracts are ambiguous raises, so the verdict can name that side."""
    with pytest.raises(architecture.ContractError, match=message):
        architecture.contracts_of(files)


A_AND_B = {"a": FORBIDDEN, "b": FORBIDDEN}
REMOVES_B = {"base_files": {".importlinter": contracts_ini(A_AND_B)}, "merge_files": {".importlinter": contracts_ini({"a": FORBIDDEN})}}
B_CONTRACT_REMOVED = 'import contract "b" removed'
FILE_REMOVED = "import-linter config in .importlinter removed"


class GhWithImportConfigs(FakeGh):
    """The door's fake gh, holding import-linter config files at the base and the merge commit."""

    def __init__(self, base_files=None, merge_files=None, **kwargs):
        super().__init__(**kwargs)
        self.files |= {(name, self.base_sha): text for name, text in (base_files or {}).items()}
        self.files |= {(name, self.merge_sha): text for name, text in (merge_files or {}).items()}


@pytest.mark.parametrize(
    ("gh_kwargs", "line"),
    [
        (REMOVES_B, B_CONTRACT_REMOVED),
        ({"base_files": {".importlinter": contracts_ini(A_AND_B)}}, FILE_REMOVED),
        ({"base_files": {"pyproject.toml": contracts_toml({"a": FORBIDDEN})},
          "merge_files": {"pyproject.toml": contracts_toml({"a": IGNORING})}}, 'import contract "a" ignores shop.db -> shop.web'),
    ],
    ids=["cell 3 a removed contract", "cell 7 the config removed", "cell 11 a new ignore"],
)
def test_the_door_refuses_an_undeclared_import_contract_loosening_next_to_legacy_red(gh_kwargs, line, capsys):
    """LOOSE-2 (ruled yes 26.10.2026): the door reads the import-linter configs at the base and the merge commit and
    refuses an undeclared loosening of them, even when it was opened for Sonar's legacy findings."""
    gh = GhWithImportConfigs(**gh_kwargs)
    assert drive(gh)[0] == 1
    assert f"    ! {line}\n" in capsys.readouterr().out
    changed_nothing(gh)


@pytest.mark.parametrize(
    ("gh_kwargs", "what"),
    [(REMOVES_B, B_CONTRACT_REMOVED), ({"base_files": REMOVES_B["base_files"]}, FILE_REMOVED)],
    ids=["cell 4 a removed contract", "cell 7 the config removed"],
)
def test_a_declared_import_contract_loosening_merges_through_the_door(gh_kwargs, what, capsys):
    """LOOSE-1, LOOSE-3 (ruled yes 26.10.2026): with Sonar green, only the declared loosening of an import contract
    is left, and the door shows the declaration and merges."""
    gh = GhWithImportConfigs(checks=SONAR_GREEN, base_config=ONE_JOB, merge_config=ONE_JOB + declaring(what), **gh_kwargs)
    assert drive(gh)[0] == 0
    assert f"    ! {what} · overnightworks/songmaker#1345 of 2026-10-20\n        reason: ruled\n" in capsys.readouterr().out
    assert gh.sequence == ["gate false", "merge", "gate true", "comment"]


def test_import_contracts_at_the_base_that_cannot_be_told_apart_cannot_be_judged_and_change_nothing(capsys):
    """#148 S3: the door names the commit whose import contracts it cannot read, and changes nothing."""
    twice = contracts_ini({"a": FORBIDDEN}) + ini_section("importlinter:contract:again", {"name": "a", **FORBIDDEN})
    gh = GhWithImportConfigs(base_files={".importlinter": twice})
    assert drive(gh)[0] == 2
    assert f"import-linter config at {BASE_SHA}: " in capsys.readouterr().out
    changed_nothing(gh)
