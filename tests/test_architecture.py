"""#61 (Q1b-6a): hygiene's import-contract rule judges a repository's import-linter contracts.

Every cell runs through `cli.main(["hygiene", ...])`; the cell numbers are the item's scenario matrix.
"""

import json
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
from quality import cli

QUALITY_ROOT = Path(__file__).resolve().parents[1]
CLEAN_LINE = "hygiene: clean (env-file, compose-port, size, import-contract)"
SHOP = {"src/shop/__init__.py": "", "src/shop/web.py": "", "src/shop/api.py": "", "src/shop/db.py": "x = 1\n"}
SHOP_DB_IMPORTS_WEB = {**SHOP, "src/shop/db.py": "import shop.web\n"}
SHOP_CYCLE = {**SHOP_DB_IMPORTS_WEB, "src/shop/web.py": "import shop.db\n"}
FORBIDDEN = {"type": "forbidden", "source_modules": ["shop.db"], "forbidden_modules": ["shop.web"]}
LAYERS = {"type": "layers", "layers": ["shop.web", "shop.db"]}
PROTECTED = {"type": "protected", "protected_modules": ["shop.web"], "allowed_importers": ["shop.api"]}
INDEPENDENCE = {"type": "independence", "modules": ["shop.web", "shop.db"]}
ACYCLIC_SIBLINGS = {"type": "acyclic_siblings", "ancestors": ["shop"]}
STALE_IGNORE = {**FORBIDDEN, "ignore_imports": ["shop.db -> shop.api"]}
STALE_IGNORE_LINE = "contract guarded ignores shop.db -> shop.api, which no import matches"
BROKEN = "import-contract .importlinter - contract guarded is broken"
REFUSED = "hygiene: import-contract cannot judge - "


def ini_section(header: str, options: dict[str, str | list[str]]) -> str:
    lines = [f"[{header}]"]
    for key, value in options.items():
        lines.append(f"{key} = {value}" if isinstance(value, str) else f"{key} =" + "".join(f"\n    {item}" for item in value))
    return "\n".join(lines) + "\n"


def ini(contract: dict, roots: tuple[str, ...] = ("shop",), **session: str | list[str]) -> str:
    """An `.importlinter` or `setup.cfg` with one contract named `guarded`."""
    return ini_section("importlinter", {"root_packages": list(roots), **session}) + ini_section(
        "importlinter:contract:one", {"name": "guarded", **contract}
    )


def toml(contract: dict, roots: tuple[str, ...] = ("shop",)) -> str:
    """A `pyproject.toml` with one contract named `guarded`; JSON strings, lists and booleans are TOML too."""
    entries = "".join(f"{key} = {json.dumps(value)}\n" for key, value in {"name": "guarded", **contract}.items())
    return f"[tool.importlinter]\nroot_packages = {json.dumps(list(roots))}\n\n[[tool.importlinter.contracts]]\n{entries}"


def run(capsys: pytest.CaptureFixture[str], path: Path) -> tuple[int, str]:
    code = cli.main(["hygiene", str(path)])
    captured = capsys.readouterr()
    return code, captured.out + captured.err


THREE_ROOTS = {
    "src/alpha/__init__.py": "", "src/alpha/top.py": "import beta.mid\n",
    "libs/beta/__init__.py": "", "libs/beta/mid.py": "import gamma.low\n",
    "tools/gamma/__init__.py": "", "tools/gamma/low.py": "",
}
ALPHA_NEVER_REACHES_GAMMA = {"type": "forbidden", "source_modules": ["alpha"], "forbidden_modules": ["gamma"]}


@pytest.mark.parametrize(
    ("files", "expected_code", "expected_lines"),
    [
        ({"src/shop/__init__.py": ""}, 0, ["import-contract: report only (no import-linter config)", CLEAN_LINE]),
        ({**SHOP, ".importlinter": ini(FORBIDDEN)}, 0, [CLEAN_LINE]),
        ({**SHOP_DB_IMPORTS_WEB, ".importlinter": ini(FORBIDDEN)}, 1, [BROKEN, "shop.db -> shop.web (l.1)"]),
        ({**SHOP_DB_IMPORTS_WEB, "pyproject.toml": toml(LAYERS)}, 1,
         ["import-contract pyproject.toml - contract guarded is broken", "shop.db -> shop.web (l.1)"]),
        ({**SHOP_DB_IMPORTS_WEB, "setup.cfg": ini(PROTECTED)}, 1,
         ["import-contract setup.cfg - contract guarded is broken", "shop.db"]),
        ({**SHOP, ".importlinter": ini(INDEPENDENCE)}, 0, [CLEAN_LINE]),
        ({**SHOP_DB_IMPORTS_WEB, ".importlinter": ini(INDEPENDENCE)}, 1, [BROKEN, "shop.db -> shop.web (l.1)"]),
        ({**SHOP_DB_IMPORTS_WEB, ".importlinter": ini(ACYCLIC_SIBLINGS)}, 0, [CLEAN_LINE]),
        ({**SHOP_CYCLE, ".importlinter": ini(ACYCLIC_SIBLINGS)}, 1, [BROKEN, "No cycles are allowed in shop."]),
        ({**SHOP, ".importlinter": ini(FORBIDDEN), "pyproject.toml": toml(FORBIDDEN)}, 2,
         [f"{REFUSED}.importlinter and pyproject.toml each hold an import-linter config; keep one"]),
        ({**SHOP, ".importlinter": ini(FORBIDDEN, roots=("ghost",))}, 2,
         [f"{REFUSED}root package ghost needs exactly one tracked ghost/__init__.py outside the tests, found: none"]),
        ({**SHOP, "lib/shop/__init__.py": "", ".importlinter": ini(FORBIDDEN)}, 2,
         [(f"{REFUSED}root package shop needs exactly one tracked shop/__init__.py outside the tests,"
           " found: lib/shop, src/shop")]),
        ({**SHOP, "tests/shop/__init__.py": "", ".importlinter": ini(FORBIDDEN)}, 0, [CLEAN_LINE]),
        ({**THREE_ROOTS, ".importlinter": ini(ALPHA_NEVER_REACHES_GAMMA, roots=("alpha", "beta", "gamma"))}, 1,
         [BROKEN, "alpha.top -> beta.mid (l.1)", "beta.mid -> gamma.low (l.1)"]),
        ({**SHOP, ".importlinter": ini({"type": "custom"})}, 2,
         [(f"{REFUSED}contract guarded in .importlinter has type custom; only the built-in types are judged"
           " (forbidden, layers, independence, protected, acyclic_siblings)")]),
        ({**SHOP, ".importlinter": ini({"type": "forbidden", "source_modules": ["shop.db"]})}, 2,
         [f"{REFUSED}contract guarded in .importlinter has invalid options (forbidden_modules: This is a required field.)"]),
        ({**SHOP, ".importlinter": ini(STALE_IGNORE)}, 1,
         [f"import-contract .importlinter - {STALE_IGNORE_LINE}", "Fix: delete the entry"]),
        ({**SHOP, "src/shop/db.py": "import sqlalchemy\n",
          ".importlinter": ini({**FORBIDDEN, "forbidden_modules": ["sqlalchemy"]}, include_external_packages="True")}, 1,
         [BROKEN, "shop.db -> sqlalchemy (l.1)"]),
    ],
    ids=[
        "cell 1 no config is report only",
        "cell 2 a kept forbidden contract",
        "cell 3 a broken forbidden contract names the chain",
        "cell 4 a broken layers contract in pyproject.toml",
        "cell 5 a broken protected contract in setup.cfg",
        "cell 6 independence kept",
        "cell 6 independence broken",
        "cell 6 acyclic_siblings kept",
        "cell 6 acyclic_siblings broken",
        "cell 7 two configs",
        "cell 8 a root package without a tracked __init__",
        "cell 9 a root package in two production directories",
        "cell 10 a root package also under tests",
        "cell 11 a chain across three source roots",
        "cell 14 an unknown contract type",
        "cell 15 a required field missing",
        "cell 16 a stale ignore_imports entry",
        "cell 21 an external forbidden module that is not installed",
    ],
)
def test_the_import_contract_rule_judges_the_contracts(build_repo, capsys, files, expected_code, expected_lines):
    """#61 rules 2-8: kept is clean, broken is exit 1 naming the contract and the chain, a config the rule cannot
    judge is exit 2 naming why, and every printed path is repository-relative."""
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
    ids=["cell 20 a root package quality already imported", "cell 20 quality from a second checkout",
         "cell 20 an untracked copy inside the repository found first"],
)
def test_a_root_package_python_finds_elsewhere_names_both_places_without_a_local_path(
        build_repo, capsys, files, untracked, expected):
    """#61 rules 4 and 6, cell 20: the collision names the tracked place repository-relative and the other place
    repository-relative inside or from its sys.path entry outside, never by a local path, and builds no graph."""
    repo = build_repo(files)
    if untracked:
        (repo / untracked).parent.mkdir(parents=True, exist_ok=True)
        (repo / untracked).write_text("")
    code, output = run(capsys, repo)
    assert code == 2, output
    assert expected in output, output
    local_places = (str(Path(os.path.realpath(module.__file__)).parent.parent) for module in (yaml, quality))
    assert [place for place in (*local_places, str(repo), "../") if place in output] == [], output


@pytest.mark.parametrize(
    ("alerting", "expected_note"),
    [("warn", f"import-contract: note - {STALE_IGNORE_LINE}"), ("none", None)],
)
def test_a_stale_ignore_follows_the_contracts_own_alerting(build_repo, capsys, alerting, expected_note):
    """#61 rule 7, cell 16b: `warn` prints one note and stays green, `none` prints nothing."""
    repo = build_repo({**SHOP, ".importlinter": ini({**STALE_IGNORE, "unmatched_ignore_imports_alerting": alerting})})
    code, output = run(capsys, repo)
    assert (code, CLEAN_LINE in output) == (0, True), output
    assert [line for line in output.splitlines() if "ignores shop.db" in line] == ([expected_note] if expected_note else [])


def test_a_dotted_root_package_never_runs_its_parent(build_repo, tmp_path, capsys, monkeypatch):
    """#61 rule 4, cell 12: finding a dotted root package would import its parent, so nothing is looked up."""
    marker = tmp_path.parent / f"{tmp_path.name}-parent-ran"
    repo = build_repo({**SHOP, "src/shop/__init__.py": f"open({str(marker)!r}, 'w').close()\n",
                       ".importlinter": ini(INDEPENDENCE, roots=("shop.web",))})
    monkeypatch.syspath_prepend(str(repo / "src"))
    code, output = run(capsys, repo)
    assert code == 2, output
    assert f"{REFUSED}root package shop.web is dotted; finding it would import shop" in output
    assert not marker.exists()


def test_a_plugin_contract_type_is_never_imported(build_repo, tmp_path, capsys, monkeypatch):
    """#61 rule 4, cell 13: a `contract_types` session option names modules import-linter would import."""
    marker = tmp_path.parent / f"{tmp_path.name}-plugin-ran"
    repo = build_repo({**SHOP, "plugin.py": f"open({str(marker)!r}, 'w').close()\n",
                       ".importlinter": ini(INDEPENDENCE, contract_types=["custom: plugin.Custom"])})
    monkeypatch.syspath_prepend(str(repo))
    code, output = run(capsys, repo)
    assert code == 2, output
    assert f"{REFUSED}.importlinter sets contract_types, whose plugin modules would be imported" in output
    assert not marker.exists()


def test_a_syntax_error_is_unparsable_and_hides_no_other_rule(build_repo, capsys):
    """#61 rule 6, cell 17 (#55): grimp cannot parse a module, so the rule is unparsable; env-file still judges."""
    repo = build_repo({**SHOP, "src/shop/bad.py": "def broken(:\n", ".env": "A=1\n", ".importlinter": ini(FORBIDDEN)})
    code, output = run(capsys, repo)
    assert code == 2, output
    assert "hygiene: import-contract unparsable - Syntax error in src/shop/bad.py, line 1" in output
    assert "env-file .env - " in output
    assert str(repo) not in output


def test_a_library_warning_is_a_repository_relative_note(build_repo, capsys):
    """#61 rule 4: grimp warns about a module it skips with an absolute path; the rule prints it as its own note."""
    repo = build_repo({**SHOP, "src/shop/some.module.py": "", ".importlinter": ini(FORBIDDEN)})
    code, output = run(capsys, repo)
    assert code == 0, output
    assert ("import-contract: note - Warning: skipping module with too many dots in the name:"
            " src/shop/some.module.py") in output
    assert str(repo) not in output


def test_a_config_linked_out_of_the_repository_cannot_be_judged(build_repo, elsewhere, capsys):
    """#61 rule 3, cell 18: the config is read through repository.read_text, which keeps it inside."""
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
    """#61 rule 4, cell 18b: grimp follows symlinks, so a link leaving the repository, directly or through a link
    that stays inside, is refused and no graph is built; the outside module would break the contract if read."""
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
    """#61 rule 4: grimp would walk a symlink loop without end, so the loop is refused, named repository-relative."""
    repo = build_repo({**SHOP, ".importlinter": ini(FORBIDDEN)})
    (repo / "src" / "shop" / link).symlink_to(target)
    code, output = run(capsys, repo)
    assert code == 2, output
    assert f"{REFUSED}{expected}; a symlink loop cannot be judged" in output
    assert "OSError" not in output


def test_a_file_link_into_a_loop_outside_the_repository_is_refused_as_leaving_it(build_repo, elsewhere, capsys):
    """#61 rule 4, cell 18b: a link that leaves the repository is named as such, even where the place it reaches loops."""
    repo = build_repo({**SHOP, ".importlinter": ini(FORBIDDEN)})
    (elsewhere / "p").symlink_to("q")
    (elsewhere / "q").symlink_to("p")
    (repo / "src" / "shop" / "x.py").symlink_to(elsewhere / "p")
    code, output = run(capsys, repo)
    assert code == 2, output
    assert f"{REFUSED}src/shop/x.py links outside the repository" in output
    assert "../" not in output


def test_a_dangling_file_link_under_a_root_package_is_named_not_blamed_on_the_config(build_repo, capsys):
    """#61 rule 4: a dangling link is named as such; no message blames the config for a file."""
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
    """#61 rule 4: no repository path, Rust panic text or config blame on either descriptor."""
    for noise in (str(repo), "panicked", "RUST_BACKTRACE", "the config", "PermissionError"):
        assert noise not in output


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a 0o444 directory, so nothing blocks the read")
@pytest.mark.parametrize("arrange", [_unreadable_file, _unreadable_python_file, _unreadable_directory_with_a_file,
                                     _empty_unreadable_directory])
def test_a_place_that_cannot_be_examined_is_cannot_judge_naming_it(build_repo, capfd, arrange):
    """#61 rule 4: a read failure other than a loop or a dangling link is exit 2 naming the place, not the config."""
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
    """#61 rule 4: a .py file that is no regular file, has a name or content grimp cannot decode, or declares an
    encoding other than UTF-8 is exit 2 naming it, with no Rust panic text, no absolute path and no hang."""
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
    """#61 rule 4: a cookie spelling grimp accepts does not stop the judgement of the forbidden import."""
    repo = build_repo({**SHOP_DB_IMPORTS_WEB, "src/shop/db.py": f"{cookie_line}import shop.web\n",
                       ".importlinter": ini(FORBIDDEN)})
    code, output = run(capfd, repo)
    assert code == 1, output
    assert f"shop.db -> shop.web (l.{import_line})" in output


@pytest.mark.parametrize("source, echoed", [("x = 1\0y = 2\n", "x = 1\\x00y = 2"),
                                            ("x = 1 +" + "+" * 400 + "(\n", "…")])
def test_the_source_text_a_syntax_error_echoes_is_escaped_and_cut(build_repo, capfd, source, echoed):
    """#61 rule 4: a control character in the echoed source is escaped and a long line is cut."""
    repo = build_repo({**SHOP, "src/shop/bad.py": source, ".importlinter": ini(FORBIDDEN)})
    code, output = run(capfd, repo)
    assert code == 2, output
    assert echoed in output
    assert "\0" not in output
    assert all(len(line) < 400 for line in output.splitlines())


def test_a_config_tracked_but_deleted_from_disk_is_none(build_repo, capsys):
    """#76 rule 4, cell 11 (flips #61 cell 19): a config counts when its name exists."""
    repo = build_repo({**SHOP, ".importlinter": ini(FORBIDDEN)})
    (repo / ".importlinter").unlink()
    code, output = run(capsys, repo)
    assert (code, "import-contract: report only (no import-linter config)" in output) == (0, True), output


def test_a_config_linked_to_nowhere_cannot_be_judged(build_repo, capsys):
    """#76 rule 4, cell 12: a name that exists but leads nowhere is a file git has and the rule cannot read."""
    repo = build_repo({**SHOP, ".importlinter": ini(FORBIDDEN)})
    (repo / ".importlinter").unlink()
    (repo / ".importlinter").symlink_to("missing.ini")
    code, output = run(capsys, repo)
    assert (code, f"{REFUSED}cannot read .importlinter" in output) == (2, True), output


def test_a_tracked_package_init_deleted_from_disk_cannot_be_judged_without_a_traceback(build_repo, capsys):
    """#76 cell 13: architecture.py is untouched, and the tree it cannot build is exit 2."""
    repo = build_repo({**SHOP_DB_IMPORTS_WEB, ".importlinter": ini(FORBIDDEN)})
    (repo / "src/shop/__init__.py").unlink()
    code, output = run(capsys, repo)
    assert (code, REFUSED in output, "Traceback" in output) == (2, True, False), output


def test_an_error_inside_a_contract_check_names_the_contract_without_a_traceback(build_repo, capsys, monkeypatch):
    """#61 rule 7, cell 20b: any other import-linter or grimp error is cannot judge, never a traceback."""
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
    """#61 rule 7: a value of the wrong type is cannot judge naming the contract or the config, never a traceback."""
    code, output = run(capsys, build_repo({**SHOP, "pyproject.toml": config}))
    assert code == 2, output
    assert f"{REFUSED}{subject} cannot be judged: " in output
    assert "Traceback" not in output


@pytest.mark.parametrize("files", [SHOP, SHOP_DB_IMPORTS_WEB], ids=["kept", "broken"])
def test_a_run_writes_nothing_and_restores_sys_path(build_repo, capsys, files):
    """#61 rules 1 and 6, cell 22: no cache, no change to the checkout, and sys.path as it was."""
    repo = build_repo({**files, ".importlinter": ini(FORBIDDEN)})
    path_before = list(sys.path)
    run(capsys, repo)
    status = subprocess.run(["git", "-C", str(repo), "status", "--porcelain", "--ignored"],
                            capture_output=True, text=True, check=True)
    assert (status.stdout, (repo / ".import_linter_cache").exists(), sys.path) == ("", False, path_before)


def test_qualitys_own_tree_holds_its_layers(capsys):
    """#61 rule 8, cell 23: quality's own layers contract is enforced and kept."""
    code, output = run(capsys, QUALITY_ROOT)
    assert (code, CLEAN_LINE in output, "report only (no import-linter config)" in output) == (0, True, False), output
