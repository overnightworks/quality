"""Each loosening stands under the verdict's `loosenings` row as its `what`, and the text a person copies from an
undeclared line declares it."""

import datetime

import pytest

from quality import output, verdict
from quality.baseline_file import BASELINE_PATH
from quality.ratchet import NO_RENAMES
from quality.report import CannotJudge
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


def secret_allowance(rule="api-key", file="src/app.py", hash_digit="a", reason="a test key"):
    """One (rule, file, secret_sha256, reason) allowance; `hash_digit` fills the first 12 hex digits of the hash."""
    return (rule, file, hash_digit * 12 + "0" * 52, reason)


def secret_blocks(*allowances: tuple[str, str, str, str]) -> str:
    """Each (rule, file, secret_sha256, reason) as a [[secrets.allow]] block."""
    return "".join(
        f'[[secrets.allow]]\nrule = "{rule}"\nfile = "{file}"\nsecret_sha256 = "{sha256}"\nreason = "{reason}"\n'
        for rule, file, sha256, reason in allowances
    )


def judged(base_config, head_config, baselines, imports):
    base = side_of(base_config, baselines[0], "the base", {".importlinter": imports[0]})
    head = side_of(head_config, baselines[1], "the head", {".importlinter": imports[1]})
    return judge(head, base, [job(), job("B")], {}, 1, NO_RENAMES)


def secret_inline(*allowances: tuple[str, str, str, str]) -> str:
    """The same entries as one inline `allow = [...]` list."""
    rows = ", ".join(
        f'{{ rule = "{rule}", file = "{file}", secret_sha256 = "{sha256}", reason = "{reason}" }}'
        for rule, file, sha256, reason in allowances
    )
    return f"[secrets]\nallow = [{rows}]\n"


def settled(base_config, head_config):
    """The verdict's loosenings as `(what, declared)`, the `what`s of its unmatched declarations, and whether it is blocked."""
    result = judged(base_config, head_config, ("", ""), (None, None))
    return [(each.what, each.declaration is not None) for each in result.loosenings], [each.what for each in result.stale], result.blocked


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


CI = ".github/workflows/ci.yml"


def jobs(**limits):
    """A `[job_minutes]` table holding `limits` for the job ids of one workflow file."""
    return f'[job_minutes."{CI}"]\n' + "".join(f"{job_id} = {minutes}\n" for job_id, minutes in limits.items())


def pages(**counts):
    """A `[literal_text]` table holding `counts` for page paths under `frontend/src/lib`, named `A` to `C`."""
    return "[literal_text]\n" + "".join(f'"frontend/src/lib/{name}.svelte" = {count}\n' for name, count in counts.items())


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
    kind(CHECKS, CHECKS, (jobs(e2e=40), jobs(e2e=45)), id="a job limit raised"),
    kind(CHECKS, CHECKS, (jobs(e2e=40), jobs(e2e=40, other=20)), id="a job limit added"),
    kind(CHECKS, CHECKS, (jobs(e2e=40), ""), id="the job limit baseline removed"),
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
    kind(CHECKS, CHECKS + secret_blocks(secret_allowance()), id="a secret allowance added"),
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
    "a job limit raised": [f"    ! job limit baseline raises {CI} e2e 40 -> 45"],
    "a job limit added": [f"    ! job limit baseline adds {CI} other 20"],
    "the job limit baseline removed": ["    ! job limit baseline removed"],
    "an import-linter config removed": ["    ! import-linter config in .importlinter removed"],
    "a session option changed": ["    ! import-linter changes root_packages"],
    "a contract removed": ['    ! import contract "guarded" removed'],
    "a contract's type changed": ['    ! import contract "guarded" changes type'],
    "an ignore added": ['    ! import contract "guarded" ignores shop.db -> shop.web'],
    "a contract option changed": ['    ! import contract "guarded" changes allow_indirect_imports'],
    "a secret allowance added": [f"    ! secret allowance adds api-key in src/app.py{output.SEPARATOR}sha256 aaaaaaaaaaaa"],
    "an advisory allowance added": [f"    ! advisory allowance adds {QWEN} until 2027-01-31 · {PRESENTATOR}", GRADIO],
    "an advisory allowance extended": [f"    ! advisory allowance extends {QWEN} to 2027-07-31 · {PRESENTATOR}", GRADIO],
    "a generated file": [
        "    ! size leaves out generated file frontend/src/lib/api/types.ts · #174 of 2026-10-30",
        "        reason: written by scripts/generate_types.py, proven by its --check",
    ],
}


@pytest.mark.parametrize(("base_config", "head_config", "baselines", "imports"), UNDECLARED_KINDS + SELF_DECLARED_KINDS)
def test_each_loosening_stands_under_its_row_as_its_what(request, base_config, head_config, baselines, imports):
    """VERDICT-53."""
    lines = printed(judged(base_config, head_config, baselines, imports))
    assert lines == PRINTED[request.node.callspec.id]


@pytest.mark.parametrize(("base_config", "head_config", "baselines", "imports"), UNDECLARED_KINDS)
def test_the_text_to_copy_declares_its_loosening(base_config, head_config, baselines, imports):
    """VERDICT-53, LOOSE-1: The copy text is everything after `! ` on an undeclared line; written into a new entry it
    declares that loosening and nothing stays unmatched."""
    (line,) = printed(judged(base_config, head_config, baselines, imports))
    copied = head_config + declaration(line.removeprefix("    ! "))
    again = judged(base_config, copied, baselines, imports)
    assert ([loosening.declaration is not None for loosening in again.loosenings], again.stale) == ([True], ())


@pytest.mark.parametrize(("base_config", "head_config", "baselines", "imports"), SELF_DECLARED_KINDS)
def test_an_allowance_or_generated_text_copied_into_a_declaration_matches_nothing(base_config, head_config, baselines, imports):
    """VERDICT-67, VERDICT-70: An advisory allowance and a generated file declare themselves: their text copied into an
    entry binds nothing."""
    first = printed(judged(base_config, head_config, baselines, imports))[0]
    what = first.removeprefix("    ! ").rsplit(output.SEPARATOR, 1)[0]
    again = judged(base_config, head_config + declaration(what), baselines, imports)
    assert ([declared.what for declared in again.stale], again.blocked) == ([what], True)


@pytest.mark.parametrize(
    ("baselines", "expected"),
    [
        pytest.param(("", jobs(e2e=40)), [], id="a base without the table: adoption"),
        pytest.param((None, jobs(e2e=40)), [], id="no base file: adoption of the file"),
        pytest.param((jobs(e2e=40), jobs(e2e=40)), [], id="equal"),
        pytest.param((jobs(e2e=40), jobs(e2e=35)), [], id="lowered"),
        pytest.param((jobs(e2e=40, other=20), jobs(e2e=40)), [], id="an entry dropped"),
        pytest.param(("", ""), [], id="neither side holds the table"),
        pytest.param(
            (file_lines("src/a.py", 800), file_lines("src/a.py", 820) + jobs(e2e=40)),
            ["    ! size baseline raises file_lines src/a.py 800 -> 820"],
            id="adoption beside a size entry that rises",
        ),
        pytest.param(
            (jobs(e2e=40), "[job_minutes]\n"), [], id="the table held and emptied",
        ),
    ],
)
def test_a_job_limit_table_adopted_equal_lowered_or_dropped_loosens_nothing(baselines, expected):
    """TIMEOUT-18, BASELINE-9, BASELINE-10."""
    assert printed(judged(CHECKS, CHECKS, baselines, (None, None))) == expected


@pytest.mark.parametrize(
    ("baselines", "expected"),
    [
        pytest.param(
            (jobs(e2e=40), jobs(e2e=45)), [f"    ! job limit baseline raises {CI} e2e 40 -> 45"], id="a base without clones, a raise"
        ),
        pytest.param(
            ("[clones]\n" + jobs(e2e=40), pair(("src/a.py", "f"), ("src/b.py", "g")) + jobs(e2e=45)),
            ["    ! clone baseline adds src/a.py f <-> src/b.py g", f"    ! job limit baseline raises {CI} e2e 40 -> 45"],
            id="a base with clones, a raise and a clone pair",
        ),
        pytest.param(
            ("[clones]\n" + jobs(e2e=40), "[clones]\n" + jobs(e2e=45)),
            [f"    ! job limit baseline raises {CI} e2e 40 -> 45"],
            id="a base with clones, a raise",
        ),
        pytest.param(
            (file_lines("src/a.py", 800) + jobs(e2e=40), file_lines("src/a.py", 820) + jobs(e2e=45)),
            ["    ! size baseline raises file_lines src/a.py 800 -> 820", f"    ! job limit baseline raises {CI} e2e 40 -> 45"],
            id="a size raise before a job raise",
        ),
        pytest.param(
            (jobs(e2e=40), ""), ["    ! job limit baseline removed"], id="a base without clones, the table removed"
        ),
        pytest.param(
            (jobs(z=10, a=10), jobs(z=20, a=20)),
            [f"    ! job limit baseline raises {CI} z 10 -> 20", f"    ! job limit baseline raises {CI} a 10 -> 20"],
            id="job raises in the head file's order",
        ),
    ],
)
def test_the_job_lines_come_after_the_size_and_clone_lines_whatever_the_base_clone_state_is(baselines, expected):
    """TIMEOUT-18."""
    assert printed(judged(CHECKS, CHECKS, baselines, (None, None))) == expected


@pytest.mark.parametrize("side", ["base", "head"])
@pytest.mark.parametrize(
    "table",
    [f'[job_minutes."{CI}"]\ne2e = 40.5\n', f'[job_minutes."{CI}"]\ne2e = -3\n', f'[job_minutes."{CI}".e2e]\nx = 3\n', "[job_minutes]\nci = 3\n"],
    ids=["a float", "a negative", "a table under a job", "a path that is no table"],
)
def test_a_malformed_job_table_on_either_side_cannot_be_judged_naming_the_side(side, table):
    """TIMEOUT-18, BASELINE-20."""
    with pytest.raises(CannotJudge, match=rf"{BASELINE_PATH} at the {side}: "):
        side_of(CHECKS, table, f"the {side}")


def test_a_job_limit_raise_judged_without_a_base_loosens_nothing():
    """TIMEOUT-18, BASELINE-27: a push or a merge group has no base, so no loosening is judged."""
    head = side_of(CHECKS, jobs(e2e=45), "the head")
    assert judge(head, None, [job(), job("B")], {}, 1, NO_RENAMES).loosenings == ()


def test_a_job_limit_typo_in_a_declaration_matches_nothing_and_leaves_the_raise_undeclared():
    """TIMEOUT-18: the declaration's `what` must equal the printed text."""
    typo = declaration(f"job limit baseline raises {CI} e2e 40 -> 46")
    again = judged(CHECKS, CHECKS + typo, (jobs(e2e=40), jobs(e2e=45)), (None, None))
    assert ([loosening.declaration for loosening in again.loosenings], [stale.what for stale in again.stale]) == (
        [None],
        [f"job limit baseline raises {CI} e2e 40 -> 46"],
    )


@pytest.mark.parametrize(
    "baselines",
    [
        pytest.param((pages(A=3), ""), id="the table held in the base and gone from the head"),
        pytest.param((pages(A=3), pages(A=5)), id="a count raised"),
        pytest.param((pages(A=3), pages(A=3, B=1)), id="a page added"),
        pytest.param(("", pages(A=3)), id="the table adopted"),
        pytest.param(('[literal_text]\n"a.svelte" = 0\n', '[literal_text]\n"a.svelte" = "3"\n'), id="a malformed table on both sides"),
    ],
)
def test_a_literal_text_table_is_ignored_so_whatever_it_holds_loosens_nothing(baselines):
    """LITERAL-16: the table is read and ignored, so no change of it, a malformed one included, is a loosening or exit 2."""
    assert printed(judged(CHECKS, CHECKS, baselines, (None, None))) == []


def secret_allowance_scenarios():
    """(base config, head config, expected) of the secret allowance loosenings: the loosenings, the stale declarations
    and whether the verdict is blocked."""
    key, other_hash = secret_allowance(), secret_allowance(hash_digit="b")
    inherited = CHECKS + secret_blocks(key)
    nothing = ([], [], False)
    key_text = f"secret allowance adds api-key in src/app.py{output.SEPARATOR}sha256 aaaaaaaaaaaa"
    other_hash_text = f"secret allowance adds api-key in src/app.py{output.SEPARATOR}sha256 bbbbbbbbbbbb"
    added = ([(key_text, False)], [], True)
    return [
        pytest.param(CHECKS, inherited, added, id="added"),
        pytest.param(CHECKS, CHECKS + secret_inline(key), added, id="added inline"),
        pytest.param(CHECKS + secret_inline(key), inherited, nothing, id="inherited, written another way"),
        pytest.param(
            None, CHECKS + secret_blocks(key, other_hash), ([(key_text, False), (other_hash_text, False)], [], True),
            id="a base without config: adoption",
        ),
        pytest.param(inherited, inherited, nothing, id="inherited"),
        pytest.param(inherited, CHECKS, nothing, id="deleted"),
        pytest.param(inherited, CHECKS + secret_blocks(secret_allowance(reason="reworded")), nothing, id="reason changed"),
        pytest.param(
            inherited, CHECKS + secret_blocks(secret_allowance(rule="token")),
            ([(f"secret allowance adds token in src/app.py{output.SEPARATOR}sha256 aaaaaaaaaaaa", False)], [], True), id="rule changed",
        ),
        pytest.param(
            inherited, CHECKS + secret_blocks(secret_allowance(file="src/other.py")),
            ([(f"secret allowance adds api-key in src/other.py{output.SEPARATOR}sha256 aaaaaaaaaaaa", False)], [], True), id="file changed",
        ),
        pytest.param(inherited, CHECKS + secret_blocks(other_hash), ([(other_hash_text, False)], [], True), id="hash changed"),
        pytest.param(
            inherited, CHECKS + secret_blocks(key, other_hash), ([(other_hash_text, False)], [], True),
            id="a second value added beside an inherited one",
        ),
    ]


@pytest.mark.parametrize(("base_config", "head_config", "expected"), secret_allowance_scenarios())
def test_a_secret_allowance_the_base_lacks_is_a_loosening_that_needs_a_declaration(base_config, head_config, expected):
    """VERDICT-71, VERDICT-72, VERDICT-73: a head (rule, file, secret_sha256) the base lacks is a loosening and, without
    a [[gate.loosened]] entry, undeclared; an inherited or deleted entry and a changed reason loosen nothing; a base
    without config, a changed rule, file or hash makes the entry new, and an inline list reads as blocks do."""
    assert settled(base_config, head_config) == expected


def test_a_declaration_with_the_secret_allowance_text_declares_it_and_stays_red_for_the_door():
    """VERDICT-71: a [[gate.loosened]] entry with that text as `what` binds the loosening, which stays red for the door."""
    text = f"secret allowance adds api-key in src/app.py{output.SEPARATOR}sha256 aaaaaaaaaaaa"
    head = CHECKS + secret_blocks(secret_allowance()) + declaration(text)
    result = judged(CHECKS, head, ("", ""), (None, None))
    assert ([(each.what, each.declaration is not None) for each in result.loosenings], result.stale, result.green) == (
        [(text, True)], (), False,
    )
