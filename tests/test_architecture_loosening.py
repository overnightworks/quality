"""The import-contract rule judges import-linter contracts, and the diff of two sides names each loosening."""

from pathlib import Path

import pytest

from quality import architecture
from quality.rules import Area
from tests.support.architecture import (
    FORBIDDEN,
    GUARDED_CHANGES,
    LAYERS,
    contracts_ini,
    contracts_toml,
    ini_section,
    loosened,
    one,
)
from tests.support.check import (
    area_fact,
    is_clean,
    leading_count,
)
from tests.support.check import check_output as run
from tests.support.merge import BASE_SHA, ONE_JOB, SONAR_GREEN, FakeGh, changed_nothing, consenting_to, declaring, drive

QUALITY_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.integration
def test_qualitys_own_tree_holds_its_layers(capsys):
    """IMPORT-1, ROW-11: quality's own layers contract is kept, and the clean row counts its contracts."""
    code, output = run(capsys, QUALITY_ROOT)
    declared = architecture.contracts_of({"pyproject.toml": (QUALITY_ROOT / "pyproject.toml").read_text()}).contracts
    assert (code, is_clean(output), leading_count(area_fact(output, Area.IMPORTS))) == (0, True, len(declared)), output


IGNORING = {**FORBIDDEN, "ignore_imports": ["shop.db -> shop.web"]}


ALLOWING_INDIRECT = {**FORBIDDEN, "allow_indirect_imports": True}


PLAIN_FILE = "[metadata]\nname = shop\n"


def session_change(option: str, before: str | None, after: str | None, *, loosens: bool):
    """A pair of INI files whose `option` goes from `before` to `after` (None leaves it out), and the lines its change reads as."""
    sides = [{} if value is None else {option: value} for value in (before, after)]
    pair = tuple({".importlinter": contracts_ini(one(FORBIDDEN), **session)} for session in sides)
    return (*pair, [architecture.session_loosening(option)] if loosens else [])


@pytest.mark.parametrize(
    ("base", "head", "expected"),
    [
        pytest.param(*session_change("include_external_packages", None, "true", loosens=False), id="include_external_packages added as true"),
        pytest.param(*session_change("include_external_packages", "false", "true", loosens=False),
                     id="include_external_packages turned on"),
        pytest.param(*session_change("include_external_packages", "true", "false", loosens=True),
                     id="include_external_packages turned off"),
        pytest.param(*session_change("include_external_packages", "true", None, loosens=True), id="include_external_packages dropped while on"),
        pytest.param(*session_change("include_external_packages", None, "false", loosens=False),
                     id="include_external_packages made false, its default"),
        pytest.param(*session_change("exclude_type_checking_imports", "true", "false", loosens=False),
                     id="exclude_type_checking_imports turned off"),
        pytest.param(*session_change("exclude_type_checking_imports", "true", None, loosens=False),
                     id="exclude_type_checking_imports dropped while on"),
        pytest.param(*session_change("exclude_type_checking_imports", "false", "true", loosens=True),
                     id="exclude_type_checking_imports turned on"),
        pytest.param(*session_change("exclude_type_checking_imports", None, "true", loosens=True),
                     id="exclude_type_checking_imports added as true"),
    ],
)
def test_a_session_option_loosens_only_in_the_direction_that_removes_imports_from_the_graph(base, head, expected):
    """LOOSE-12, LOOSE-25, LOOSE-34, LOOSE-37."""
    assert loosened(base, head) == expected


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
        "a removed contract in INI", "a removed contract in TOML", "the file deleted is one line",
        "setup.cfg loses its section", "pyproject.toml loses its table", "a config that holds no contract",
        "a new ignore", "two new ignores", "a wildcard is a new expression",
        "other spacing around the arrow", "a removed ignore", "allow_indirect_imports",
        "alerting", "type", "a module off source_modules", "layers in another order",
        "root_packages",
        "root order and duplicates", "a new contract and a removed ignore", "reworded guidance",
        "INI to TOML", "setup.cfg to .importlinter", "a rename", "adoption",
        "no config at either side", "several changes, one line each, sorted",
    ],
)
def test_the_contract_diff_names_each_loosening_on_its_own_line(base, head, expected):
    """LOOSE-4, LOOSE-5, LOOSE-6, LOOSE-7, LOOSE-8, LOOSE-9, LOOSE-10, LOOSE-11, LOOSE-12, LOOSE-13, LOOSE-14,
    LOOSE-15, LOOSE-31, LOOSE-32, LOOSE-33, LOOSE-34: the contracts of two sides differ by the lines the rule names,
    one each and sorted, and by no other."""
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
    ids=["a duplicate name in INI", "a duplicate name in TOML", "two configs", "a root package that is a list"],
)
def test_a_side_that_names_a_contract_twice_or_holds_two_configs_cannot_be_judged(files, message):
    """LOOSE-16, LOOSE-17, LOOSE-18: a side whose contracts are ambiguous or whose config cannot be read raises, so
    the verdict can name that side."""
    with pytest.raises(architecture.ContractError, match=message):
        architecture.contracts_of(files)


A_AND_B = {"a": FORBIDDEN, "b": FORBIDDEN}


CONFIG_AT_BASE = {"base_files": {".importlinter": contracts_ini(A_AND_B)}}


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
        (CONFIG_AT_BASE, FILE_REMOVED),
        ({"base_files": {"pyproject.toml": contracts_toml({"a": FORBIDDEN})},
          "merge_files": {"pyproject.toml": contracts_toml({"a": IGNORING})}}, 'import contract "a" ignores shop.db -> shop.web'),
    ],
    ids=["the config removed", "a new ignore"],
)
def test_the_door_refuses_an_undeclared_import_contract_loosening_next_to_legacy_red(gh_kwargs, line, capsys):
    """LOOSE-2: the door reads the import-linter configs at the base and the merge commit and refuses an undeclared
    loosening of them, even when it was opened for Sonar's legacy findings."""
    gh = GhWithImportConfigs(**gh_kwargs)
    assert drive(gh) == 1
    assert f"    ! {line}\n" in capsys.readouterr().out
    changed_nothing(gh)


def test_a_declared_import_contract_config_removal_merges_through_the_door(capsys):
    """LOOSE-1, LOOSE-3: with Sonar green, only the declared loosening of an import contract is left, and the door
    shows the declaration and merges."""
    gh = GhWithImportConfigs(
        checks=SONAR_GREEN, base_config=ONE_JOB, merge_config=ONE_JOB + declaring(FILE_REMOVED), reviews=consenting_to(FILE_REMOVED),
        **CONFIG_AT_BASE,
    )
    assert drive(gh) == 0
    assert f"    ! {FILE_REMOVED} · overnightworks/songmaker#1345 of 2026-10-20\n        reason: ruled\n" in capsys.readouterr().out
    assert gh.sequence == ["gate false", "merge", "gate true", "comment"]


def test_import_contracts_at_the_base_that_cannot_be_told_apart_cannot_be_judged_and_change_nothing(capsys):
    """LOOSE-16: the door names the commit whose import contracts it cannot read, and changes nothing."""
    twice = contracts_ini({"a": FORBIDDEN}) + ini_section("importlinter:contract:again", {"name": "a", **FORBIDDEN})
    gh = GhWithImportConfigs(base_files={".importlinter": twice})
    assert drive(gh) == 2
    assert f"import-linter config at {BASE_SHA}: " in capsys.readouterr().out
    changed_nothing(gh)
