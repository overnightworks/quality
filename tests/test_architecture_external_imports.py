"""IMPORT-29 to IMPORT-32: a forbidden contract that names a submodule of an external package in its ignore_imports
judges the package at the submodules it names."""

import pytest

from quality import architecture
from quality.report import FindingEvent
from tests.support.architecture import (
    calling_and_member,
    core_keeps_to_two_of_agents,
    judged_core,
    judged_in,
    one,
    skat_core_path,
    skat_files,
)


def unmatched_ignored() -> dict:
    return core_keeps_to_two_of_agents(unmatched_ignore_imports_alerting="none")


TYPE_CHECKED = "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from agents.turn import T\n"


@pytest.mark.parametrize("core", [
    "from agents import calling as core\nfrom agents.member import Standing\n",
    "import agents.calling\nimport agents.member as member\n",
    "from agents.calling.sub import x\nfrom agents.member.deep import y\n",
    "from agents import calling, member\n",
    "from agents import (\n    calling,\n    member,\n)\n",
    "import os, agents.calling\nimport agents.member\nfrom . import sibling\n",
], ids=[
    "a submodule imported from the package", "plain imports of both", "a module below each named submodule",
    "both names in one statement", "a parenthesised statement over several lines",
    "a statement that also names another package and a relative import",
])
def test_the_core_importing_only_the_named_submodules_is_clean(tmp_path, core):
    """IMPORT-29, IMPORT-30: an import held at a submodule the contract's ignore_imports name is ignored; a module
    below it is held at it."""
    result = judged_core(tmp_path, core)
    assert (result.findings, result.notes) == ([], ())


@pytest.mark.parametrize(("core", "line"), [
    ("from agents.turn import T\n", 1),
    ("import agents.turn\n", 1),
    ("from agents import Standing\n", 1),
    ("from agents import calling, turn\n", 1),
    ("from agents import *\n", 1),
    ("import agents\n", 1),
    ("x = 1\n\n\ndef late():\n    from agents.turn import T\n", 5),
    ("from agents import (\n    calling,\n    turn,\n)\n", 1),
    ("import agents.calling; import agents.turn\n", 1),
    ("import agents.calling\nimport agents.member\nimport agents.turn as turn\n", 3),
    ("from . import sibling; import agents.turn\n", 1),
], ids=[
    "a module outside the two", "a plain import of another module", "a name of the package", "one name beside a named one",
    "a star import", "the bare package", "an import inside a function", "a parenthesised import over several lines",
    "two statements on one line", "the third of three lines", "a relative import on the same line",
])
def test_any_other_import_of_the_package_breaks_the_contract_and_names_its_line(tmp_path, core, line):
    """IMPORT-29, IMPORT-30: an import not held at a named submodule is held at the package, which the contract
    forbids."""
    (finding,) = judged_core(tmp_path, core, unmatched_ignored()).findings
    assert f"skat.table.core -> agents (l.{line})" in "\n".join(finding.detail)
    assert str(tmp_path) not in "\n".join((finding.fact, *finding.detail))


def test_an_import_under_type_checking_is_left_out_as_grimp_leaves_it_out(tmp_path):
    """IMPORT-30: with exclude_type_checking_imports the import under TYPE_CHECKING is no import of the package."""
    kept = judged_core(tmp_path, TYPE_CHECKED, unmatched_ignored(), exclude_type_checking_imports=True)
    broken = judged_core(tmp_path, TYPE_CHECKED, unmatched_ignored())
    assert (kept.findings, len(broken.findings)) == ([], 1)


def test_a_chain_from_the_core_through_another_module_to_the_package_breaks(tmp_path):
    """IMPORT-30: the refinement applies to every importer of the package, not only to the contract's source."""
    files = skat_files("import skat.helper\n", helper="from agents.turn import T\n")
    (finding,) = judged_in(tmp_path, files, one(unmatched_ignored())).findings
    assert [part in "\n".join(finding.detail) for part in ("skat.table.core -> skat.helper", "skat.helper -> agents (l.1)")] == [True, True]


@pytest.mark.parametrize(("core", "unused"), [
    ("from agents import calling\n", ["skat.table.** -> agents.member"]),
    ("", calling_and_member()),
], ids=["a line no import matches", "no import of the package at all"])
def test_a_submodule_line_no_import_matches_is_unused(tmp_path, core, unused):
    """IMPORT-6, IMPORT-29: an ignore line that matches no import is a finding at the contract's alerting level."""
    findings = judged_core(tmp_path, core).findings
    assert sorted(finding.fact for finding in findings) == [f"contract guarded ignores {line}" for line in unused]
    assert {finding.event for finding in findings} == {FindingEvent.UNUSED}


def test_an_import_is_held_at_the_longest_named_submodule_it_lies_under(tmp_path):
    """IMPORT-30: with a submodule named beside one below it, each import is held at the longest name, so both lines
    are used."""
    contract = core_keeps_to_two_of_agents(ignore_imports=[
        "skat.table.** -> agents.calling", "skat.table.** -> agents.calling.deep",
    ])
    core = "from agents.calling.deep import x\nfrom agents.calling.other import y\n"
    assert judged_core(tmp_path, core, contract).findings == []


def test_a_bare_package_line_beside_the_two_lines_is_unused_when_only_the_two_are_imported(tmp_path):
    """IMPORT-31: on a refined copy the bare line matches only imports held at the package itself."""
    contract = core_keeps_to_two_of_agents(ignore_imports=[*calling_and_member(), "skat.table.** -> agents"])
    core = "from agents import calling\nfrom agents.member import Standing\n"
    (finding,) = judged_core(tmp_path, core, contract).findings
    assert (finding.event, finding.fact) == (FindingEvent.UNUSED, "contract guarded ignores skat.table.** -> agents")


def test_a_bare_package_line_still_covers_an_import_held_at_the_package(tmp_path):
    """IMPORT-31: an import held at the package is covered by the bare line."""
    contract = core_keeps_to_two_of_agents(ignore_imports=[*calling_and_member(), "skat.table.** -> agents"])
    core = "from agents import calling, Standing\nfrom agents.member import X\n"
    assert judged_core(tmp_path, core, contract).findings == []


def test_another_contracts_bare_package_line_matches_on_its_squashed_copy(tmp_path):
    """IMPORT-29: a contract with no submodule line judges the squashed package, beside a refining contract."""
    web = {"type": "forbidden", "source_modules": ["skat.web"], "forbidden_modules": ["agents"],
           "ignore_imports": ["skat.web -> agents"]}
    files = skat_files("import agents.calling\nimport agents.member\n", web="import agents.turn\n")
    assert judged_in(tmp_path, files, {"core": core_keeps_to_two_of_agents(), "web": web}).findings == []


def test_a_wildcard_line_names_no_submodule_so_the_package_stays_squashed(tmp_path):
    """IMPORT-29: `-> agents.*` matches nothing of a squashed package and is unused, as in import-linter."""
    contract = core_keeps_to_two_of_agents(ignore_imports=["skat.table.** -> agents.*"])
    (finding,) = judged_core(tmp_path, "import agents.calling\n", contract).findings
    assert (finding.event, finding.fact) == (FindingEvent.UNUSED, "contract guarded ignores skat.table.** -> agents.*")


def test_an_independence_and_a_layers_contract_are_judged_as_before(tmp_path):
    """IMPORT-29: only a forbidden contract is refined; the others judge the squashed package."""
    independence = {"type": "independence", "modules": ["skat.web", "skat.helper"], "ignore_imports": ["skat.web -> agents.calling"]}
    layers = {"type": "layers", "layers": ["skat.web", "skat.table"]}
    files = skat_files("import agents.calling\nimport agents.member\n", web="import agents.calling\n")
    result = judged_in(tmp_path, files, {"core": core_keeps_to_two_of_agents(), "independence": independence, "layers": layers})
    assert [(finding.fact, finding.event) for finding in result.findings] == [
        ("contract independence ignores skat.web -> agents.calling", FindingEvent.UNUSED)]


def test_a_refining_contract_with_as_packages_false_cannot_be_judged(tmp_path):
    """IMPORT-32: the contract that forbids `agents` and ignores two of its submodules cannot say what as_packages = false
    means for an import no line covers."""
    contract = core_keeps_to_two_of_agents(source_modules=["skat.web"], as_packages=False)
    files = skat_files(web="import agents.calling\n")
    with pytest.raises(architecture.ContractError, match="as_packages") as refused:
        judged_in(tmp_path, files, one(contract))
    assert str(tmp_path) not in str(refused.value)


@pytest.mark.parametrize(("files", "untracked", "tracked_absent", "refusal"), [
    (skat_files("import agents.calling\nimport agents.member\nx = 1" + "0" * 5000 + "\n"), (), (),
     "src/skat/table/core.py is not valid Python"),
    (skat_files("import agents.turn\n"), (skat_core_path(),), (), "module skat.table.core cannot be examined"),
    ({**{name: content for name, content in skat_files().items() if name != skat_core_path()},
      "src/skat/table/core/__init__.py": "import agents.turn\n"},
     ("src/skat/table/core/__init__.py",), (skat_core_path(),),
     "module skat.table.core cannot be examined: the checkout holds nothing at src/skat/table/core.py"),
], ids=["a module grimp reads and Python does not parse", "a module the checkout does not track",
        "a module git tracks as a file and the checkout holds as a package"])
def test_a_module_that_cannot_be_read_is_refused_without_a_local_path(tmp_path, files, untracked, tracked_absent, refusal):
    """IMPORT-32: the statements of a module that Python cannot parse, that git does not track or whose tracked file the
    checkout does not hold cannot be judged."""
    contracts = one(unmatched_ignored())
    with pytest.raises(architecture.ContractError) as refused:
        judged_in(tmp_path, files, contracts, untracked, tracked_absent)
    assert refusal in str(refused.value)
    assert str(tmp_path) not in str(refused.value)
