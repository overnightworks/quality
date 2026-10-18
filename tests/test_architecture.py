"""#61 (Q1b-6a): hygiene's import-contract rule judges a repository's import-linter contracts.

Every cell runs through `cli.main(["hygiene", ...])`; the cell numbers are the item's scenario matrix.
"""

import json
import subprocess
import sys
from pathlib import Path

import grimp
import pytest
from importlinter.contracts.forbidden import ForbiddenContract

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


@pytest.fixture
def elsewhere(tmp_path_factory) -> Path:
    """A scratch directory outside the repository, which build_repo builds in tmp_path."""
    return tmp_path_factory.mktemp("elsewhere")


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
        ({"yaml/__init__.py": "", ".importlinter": ini({"type": "independence", "modules": ["yaml.a"]}, roots=("yaml",))},
         2, [f"{REFUSED}root package yaml is yaml/__init__.py, but Python finds it outside the repository"]),
        ({"src/quality/__init__.py": "",
          ".importlinter": ini({"type": "independence", "modules": ["quality.a"]}, roots=("quality",))},
         2, [f"{REFUSED}root package quality is src/quality/__init__.py, but Python finds it outside the repository"]),
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
        "cell 20 a root package quality already imported",
        "cell 20 quality from a second checkout",
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


def test_a_symlink_loop_under_a_root_package_cannot_be_judged(build_repo, capsys):
    """#61 rule 4: grimp would walk a symlink loop without end, so the loop is refused, named repository-relative."""
    repo = build_repo({**SHOP, ".importlinter": ini(FORBIDDEN)})
    (repo / "src" / "shop" / "again").symlink_to(".")
    code, output = run(capsys, repo)
    assert code == 2, output
    assert f"{REFUSED}src/shop/again links back into src/shop; a symlink loop cannot be judged" in output


def test_a_config_tracked_but_missing_on_disk_cannot_be_judged(build_repo, capsys):
    """#61 rule 3, cell 19 (until Q1b-3d)."""
    repo = build_repo({**SHOP, ".importlinter": ini(FORBIDDEN)})
    (repo / ".importlinter").unlink()
    code, output = run(capsys, repo)
    assert code == 2, output
    assert f"{REFUSED}.importlinter is tracked but not on disk" in output


def test_an_error_inside_a_contract_check_names_the_contract_without_a_traceback(build_repo, capsys, monkeypatch):
    """#61 rule 7, cell 20b: any other import-linter or grimp error is cannot judge, never a traceback."""
    def raising_check(contract, graph, verbose):
        raise RuntimeError("boom")

    monkeypatch.setattr(ForbiddenContract, "check", raising_check)
    code, output = run(capsys, build_repo({**SHOP, ".importlinter": ini(FORBIDDEN)}))
    assert code == 2, output
    assert f"{REFUSED}contract guarded cannot be judged: RuntimeError: boom" in output
    assert "Traceback" not in output


@pytest.mark.parametrize(
    ("config", "expected"),
    [(toml({"type": ["forbidden"]}), "contract guarded in pyproject.toml cannot be judged: TypeError: "),
     (toml({**FORBIDDEN, "as_packages": 1}), "contract guarded in pyproject.toml cannot be judged: AttributeError: "),
     (toml(FORBIDDEN, roots=(1,)), "the config pyproject.toml cannot be judged: TypeError: ")],
    ids=["a type that is a list", "a boolean option that is a number", "a root package that is a number"],
)
def test_a_malformed_config_value_cannot_be_judged_without_a_traceback(build_repo, capsys, config, expected):
    """#61 rule 7: a value of the wrong type is cannot judge naming the contract or the config, never a traceback."""
    code, output = run(capsys, build_repo({**SHOP, "pyproject.toml": config}))
    assert code == 2, output
    assert f"{REFUSED}{expected}" in output
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
