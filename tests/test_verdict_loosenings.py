"""Each loosening stands under the verdict's `loosenings` row as its `what`, and the text a person copies from an
undeclared line declares it."""

import datetime

import pytest

from quality import output, verdict
from quality.ratchet import NO_RENAMES
from quality.sides import side_of
from quality.verdict import judge
from tests.support.advisories import allowance_config
from tests.support.architecture import FORBIDDEN, contracts_ini
from tests.support.generated_files import generated_config
from tests.support.verdict import declaration, gate_config, job

pytestmark = pytest.mark.unit

CHECKS = gate_config("checks")
GUARDED = contracts_ini({"guarded": FORBIDDEN})
QWEN = "GHSA-462x-pgg6-55fp in speech/providers/qwen/uv.lock"
PRESENTATOR = "overnightworks/agent-presentator#371 of 2026-10-21"
GRADIO = "        reason: gradio caps pillow<13; no image input"


def kind(base_config, head_config, baselines=("", ""), imports=(None, None), *, id):
    """One kind of loosening: the base and head config, baseline and `.importlinter` texts."""
    return pytest.param(base_config, head_config, baselines, imports, id=id)


def judged(base_config, head_config, baselines, imports):
    base = side_of(base_config, baselines[0], "the base", {".importlinter": imports[0]})
    head = side_of(head_config, baselines[1], "the head", {".importlinter": imports[1]})
    return judge(head, base, [job(), job("B")], {}, 1, NO_RENAMES)


def printed(judged_verdict) -> list[str]:
    """The lines under the verdict's rows, as a pipe shows them."""
    pull_request = verdict.PullRequest(7, "main", "4e1a9c2" + "0" * 33, "5d1e0b7" + "0" * 33)
    rows = verdict.verdict_rows(judged_verdict, verdict.GateRun("acme/widgets", "1", 1, "t", {}, pull_request), 1, None)
    lines = output.rendered(lambda channels: output.write_rows(rows, channels.out))
    return [line for line in lines if line.startswith("    ")]


def file_lines(path, count):
    return f'[file_lines]\n"{path}" = {count}\n'


def symbol(metric, path, name, count):
    return f'[{metric}."{path}"]\n"{name}" = {count}\n'


def pair(first, second):
    return f'[clones."{first[0]}"]\n{first[1]} = [["{second[0]}", "{second[1]}"]]\n'


UNDECLARED_KINDS = [
    kind(gate_config("checks", "B"), CHECKS, id="a mandatory job removed"),
    kind(gate_config("checks", legacy="fail"), CHECKS, id="legacy lowered"),
    kind(CHECKS, CHECKS + "sonar = false\n", id="sonar off"),
    kind(CHECKS, CHECKS, (file_lines("src/a.py", 820), None), id="the baseline removed"),
    kind(CHECKS, CHECKS, ("", file_lines("src/a.py", 820)), id="a file ceiling added"),
    kind(CHECKS, CHECKS, (file_lines("src/shop/orders.py", 766), file_lines("src/shop/orders.py", 790)), id="a file ceiling raised"),
    kind(CHECKS, CHECKS, ("", symbol("function_lines", "src/a.py", "f", 70)), id="a function ceiling added"),
    kind(CHECKS, CHECKS, (symbol("function_lines", "src/shop/orders.py", "place_order", 64),
                          symbol("function_lines", "src/shop/orders.py", "place_order", 71)), id="a function ceiling raised"),
    kind(CHECKS, CHECKS, ("", symbol("ccn", "src/a.py", "f", 18)), id="a CCN ceiling added"),
    kind(CHECKS, CHECKS, (symbol("ccn", "src/a.py", "f", 18), symbol("ccn", "src/a.py", "f", 20)), id="a CCN ceiling raised"),
    kind(CHECKS, CHECKS, (pair(("src/a.py", "f"), ("src/b.py", "g")), ""), id="the clone baseline removed"),
    kind(CHECKS, CHECKS, ("[clones]\n", pair(("src/a.py", "f"), ("src/b.py", "g"))), id="a clone pair added"),
    kind(CHECKS, CHECKS, imports=(GUARDED, None), id="an import-linter config removed"),
    kind(CHECKS, CHECKS, imports=(GUARDED, contracts_ini({"guarded": FORBIDDEN}, roots=("shop", "extra"))),
         id="a session option changed"),
    kind(CHECKS, CHECKS, imports=(GUARDED, contracts_ini({"other": FORBIDDEN})), id="a contract removed"),
    kind(CHECKS, CHECKS, imports=(GUARDED, contracts_ini({"guarded": {"type": "layers", "layers": ["shop.web", "shop.db"]}})),
         id="a contract's type changed"),
    kind(CHECKS, CHECKS, imports=(GUARDED, contracts_ini({"guarded": {**FORBIDDEN, "ignore_imports": ["shop.db -> shop.web"]}})),
         id="an ignore added"),
    kind(CHECKS, CHECKS, imports=(GUARDED, contracts_ini({"guarded": {**FORBIDDEN, "allow_indirect_imports": True}})),
         id="a contract option changed"),
]
SELF_DECLARED_KINDS = [
    kind(CHECKS, CHECKS + allowance_config(), id="an advisory allowance added"),
    kind(CHECKS + allowance_config(), CHECKS + allowance_config(until=datetime.date(2027, 7, 31)), id="an advisory allowance extended"),
    kind(CHECKS, CHECKS + generated_config(), id="a generated file"),
]
PRINTED = {
    "a mandatory job removed": ['    ! mandatory job "B" removed'],
    "legacy lowered": ["    ! legacy fail → report"],
    "sonar off": ["    ! sonar true → false"],
    "the baseline removed": ["    ! quality/baseline.toml removed"],
    "a file ceiling added": ["    ! size baseline adds file_lines src/a.py 820"],
    "a file ceiling raised": ["    ! size baseline raises file_lines src/shop/orders.py 766 -> 790"],
    "a function ceiling added": ["    ! size baseline adds function_lines src/a.py f 70"],
    "a function ceiling raised": ["    ! size baseline raises function_lines src/shop/orders.py place_order 64 -> 71"],
    "a CCN ceiling added": ["    ! size baseline adds ccn src/a.py f 18"],
    "a CCN ceiling raised": ["    ! size baseline raises ccn src/a.py f 18 -> 20"],
    "the clone baseline removed": ["    ! clone baseline removed"],
    "a clone pair added": ["    ! clone baseline adds src/a.py f <-> src/b.py g"],
    "an import-linter config removed": ["    ! import-linter config in .importlinter removed"],
    "a session option changed": ["    ! import-linter changes root_packages"],
    "a contract removed": ['    ! import contract "guarded" removed'],
    "a contract's type changed": ['    ! import contract "guarded" changes type'],
    "an ignore added": ['    ! import contract "guarded" ignores shop.db -> shop.web'],
    "a contract option changed": ['    ! import contract "guarded" changes allow_indirect_imports'],
    "an advisory allowance added": [f"    ! advisory allowance adds {QWEN} until 2027-01-31 · {PRESENTATOR}", GRADIO],
    "an advisory allowance extended": [f"    ! advisory allowance extends {QWEN} to 2027-07-31 · {PRESENTATOR}", GRADIO],
    "a generated file": [
        "    ! size leaves out generated file frontend/src/lib/api/types.ts · #174 of 2026-10-30",
        "        reason: written by scripts/generate_types.py, proven by its --check",
    ],
}


@pytest.mark.parametrize(("base_config", "head_config", "baselines", "imports"), UNDECLARED_KINDS + SELF_DECLARED_KINDS)
def test_each_loosening_stands_under_its_row_as_its_what(request, base_config, head_config, baselines, imports):
    lines = printed(judged(base_config, head_config, baselines, imports))
    assert lines == PRINTED[request.node.callspec.id]


@pytest.mark.parametrize(("base_config", "head_config", "baselines", "imports"), UNDECLARED_KINDS)
def test_the_text_to_copy_declares_its_loosening(base_config, head_config, baselines, imports):
    """The copy text is everything after `! ` on an undeclared line; written into a new entry it declares that
    loosening and nothing stays unmatched."""
    (line,) = printed(judged(base_config, head_config, baselines, imports))
    copied = head_config + declaration(line.removeprefix("    ! "))
    again = judged(base_config, copied, baselines, imports)
    assert ([loosening.declaration is not None for loosening in again.loosenings], again.stale) == ([True], ())


@pytest.mark.parametrize(("base_config", "head_config", "baselines", "imports"), SELF_DECLARED_KINDS)
def test_an_allowance_or_generated_text_copied_into_a_declaration_matches_nothing(base_config, head_config, baselines, imports):
    """An advisory allowance and a generated file declare themselves: their text copied into an entry binds nothing."""
    first = printed(judged(base_config, head_config, baselines, imports))[0]
    what = first.removeprefix("    ! ").rsplit(output.SEPARATOR, 1)[0]
    again = judged(base_config, head_config + declaration(what), baselines, imports)
    assert ([declared.what for declared in again.stale], again.blocked) == ([what], True)
