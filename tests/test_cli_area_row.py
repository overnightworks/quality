import pytest

from quality import check, cli
from quality.output import SEPARATOR, Glyph
from quality.report import CannotJudge, Command, LockExtent, Note, NoteEvent
from quality.rules import Area, Rule
from tests.support.check import extent_counts, leading_count


def areas_refusing(*reasons_by_area: tuple[CannotJudge, ...]) -> tuple[check.AreaVerdict, ...]:
    rules = iter(Rule)
    return tuple(
        check.AreaVerdict(area, tuple(check.RuleVerdict(next(rules), refusal=refusal) for refusal in refusals))
        for area, refusals in zip(Area, reasons_by_area, strict=False)
    )


ONE, TWO = Command("one"), Command("two")


@pytest.mark.parametrize(
    ("areas", "expected"),
    [
        (((CannotJudge("a"),),), (cli.REFUSED_AREAS_NEXT,)),
        (((CannotJudge("a", (ONE,)),), (CannotJudge("b", (ONE,)),)), (ONE,)),
        (((CannotJudge("a", (ONE,)),), (CannotJudge("b"),)), (ONE, cli.REFUSED_AREAS_NEXT)),
        (((CannotJudge("a", (TWO,)),), (CannotJudge("b", (ONE,)),)), (TWO, ONE)),
        (((CannotJudge("a", (TWO, ONE)),),), (TWO, ONE)),
    ],
    ids=["none carries a step", "one step shared", "one step beside a refusal without one", "two steps in row order", "one reason with two steps"],
)
def test_next_names_each_distinct_step_once_in_row_order_and_the_general_instruction_last_where_a_refusal_has_none(areas, expected):
    """REFUSAL-8, REFUSAL-9, REFUSAL-12, REFUSAL-13: `next:` names each distinct step once in row order, the general
    instruction last where a refusal has none and alone where none has one."""
    assert cli.next_steps(areas_refusing(*areas)) == expected


def test_an_area_names_one_fact_once_for_two_reasons_that_differ_only_in_steps():
    """REFUSAL-4: two reasons that differ only in their steps make one fact on the row, not a list of reasons."""
    area = areas_refusing((CannotJudge("same", (ONE,)), CannotJudge("same", (TWO,))))[0]
    row = cli.area_row(area, "")
    assert (row.fact, row.sub_lines) == ("same", ())


def allowed_note(path: str) -> Note:
    return Note(path, NoteEvent.ALLOWED, "a fact")


def files_area(*notes: Note) -> check.AreaVerdict:
    return check.AreaVerdict(Area.FILES, (check.RuleVerdict(Rule.PYTHON_FLOOR, notes=notes),))


def test_a_clean_row_carries_the_extent():
    """ROW-9: a clean area row carries the extent the caller gives it."""
    row = cli.area_row(files_area(), "an extent")
    assert (row.glyph, row.fact) == (Glyph.CLEAN, "an extent")


@pytest.mark.parametrize("count", [1, 2])
def test_a_clean_row_ends_with_the_count_of_its_allowed_notes(count):
    """O15: a clean row ends with the count of its allowed notes."""
    notes = tuple(allowed_note(f"{number}.toml") for number in range(count))
    *extent, allowed = cli.area_row(files_area(*notes), "an extent").fact.split(SEPARATOR)
    assert (extent, allowed.split()) == (["an extent"], [str(count), NoteEvent.ALLOWED])


def test_a_clean_row_without_an_extent_or_notes_has_no_fact():
    """ROW-9: a clean row with neither an extent nor an allowed note has no fact."""
    assert cli.area_row(files_area(), "").fact == ""


def debt_area(*, size_measured_only: bool) -> check.AreaVerdict:
    return check.AreaVerdict(
        Area.DEBT,
        (check.RuleVerdict(Rule.SIZE, measured_only=size_measured_only), check.RuleVerdict(Rule.CLONE, measured_only=True)),
    )


@pytest.mark.parametrize("size_measured_only", [True, False], ids=["measured only", "partly measured only"])
def test_a_measured_only_row_names_the_extent_then_the_mode(size_measured_only):
    """O21: a debt row that only measured is a `not recorded` row naming its extent, then its mode."""
    row = cli.area_row(debt_area(size_measured_only=size_measured_only), "an extent")
    extent, mode = row.fact.split(SEPARATOR)
    assert (row.event, extent) == (cli.AreaEvent.NOT_RECORDED, "an extent")
    assert mode


def test_the_partly_mode_contains_the_mode_of_a_row_measured_only_throughout():
    """O21: the mode of a debt row that measured partly contains the mode of one that measured throughout."""
    full = cli.area_row(debt_area(size_measured_only=True), "").fact
    partly = cli.area_row(debt_area(size_measured_only=False), "").fact
    assert (full in partly, full == partly) == (True, False)


def imports_area(*, extent: int | None, measured_only: bool = False) -> check.AreaVerdict:
    return check.AreaVerdict(Area.IMPORTS, (check.RuleVerdict(Rule.IMPORT_CONTRACT, measured_only=measured_only, extent=extent),))


def test_an_imports_area_that_only_measured_is_a_note_row_carrying_the_fact_it_is_given():
    """ROW-13: an imports area that only measured is a note row carrying the fact it is given."""
    row = cli.area_row(imports_area(extent=0, measured_only=True), "a fact")
    assert (row.glyph, row.event, row.fact) == (Glyph.NOTE, cli.AreaEvent.NOT_SET_UP, "a fact")


@pytest.mark.parametrize("extent", [0, 1, 2])
def test_a_judged_imports_area_opens_its_extent_with_the_verdicts_extent(extent):
    """ROW-11: a judged imports area opens its extent with the contract count of its verdict."""
    assert leading_count(cli.area_extent(imports_area(extent=extent), 0)) == extent


def secrets_area(extent: int | None, refusal: CannotJudge | None = None) -> check.AreaVerdict:
    return check.AreaVerdict(Area.SECRETS, (check.RuleVerdict(Rule.LEAK, extent=extent, refusal=refusal),))


@pytest.mark.parametrize("extent", [0, 1, 412])
def test_a_judged_secrets_area_opens_its_extent_with_the_verdicts_extent(extent):
    """O30: a judged secrets area opens its extent with the commit count of its verdict."""
    assert leading_count(cli.area_extent(secrets_area(extent), 0)) == extent


def test_a_secrets_area_that_could_not_check_has_no_extent():
    """REFUSAL-2: a secrets area that could not check has no extent."""
    assert cli.area_extent(secrets_area(None, CannotJudge("why")), 0) == ""


def test_an_imports_area_that_could_not_check_has_no_extent():
    """REFUSAL-2: an imports area that could not check has no extent."""
    area = check.AreaVerdict(Area.IMPORTS, (check.RuleVerdict(Rule.IMPORT_CONTRACT, refusal=CannotJudge("why")),))
    assert cli.area_extent(area, 0) == ""


@pytest.mark.parametrize("size_measured_only", [True, False], ids=["measured debt", "partly measured debt"])
def test_each_area_that_only_measured_has_its_own_note_in_area_order(size_measured_only):
    areas = (debt_area(size_measured_only=size_measured_only), imports_area(extent=0, measured_only=True))
    debt_note, imports_note = cli.measured_notes(areas)
    assert (debt_note.startswith(Area.DEBT), imports_note.startswith(Area.IMPORTS), debt_note != imports_note) == (True, True, True)


def judging(rule: Rule, extent: int | None) -> check.RuleVerdict:
    return check.RuleVerdict(rule, extent=extent, refusal=CannotJudge("why") if extent is None else None)


def workflows_area(lint: int | None, security: int | None) -> check.AreaVerdict:
    return check.AreaVerdict(Area.WORKFLOWS, (judging(Rule.WORKFLOW_LINT, lint), judging(Rule.WORKFLOW_SECURITY, security)))


@pytest.mark.parametrize("extent", [0, 1, 5])
def test_a_judged_workflows_area_opens_its_extent_with_the_verdicts_extent_and_ends_with_the_offline_audits(extent):
    """O38: a judged workflows area opens its extent with the workflow count and ends with the offline audits."""
    fact = cli.area_extent(workflows_area(extent, extent), 0)
    assert (leading_count(fact), fact.endswith(cli.OFFLINE_AUDITS)) == (extent, True)


@pytest.mark.parametrize(
    ("area", "first", "second"),
    [(Area.WORKFLOWS, Rule.WORKFLOW_LINT, Rule.WORKFLOW_SECURITY), (Area.CODE, Rule.PYTHON_SECURITY, Rule.PYTHON_PRINT)],
    ids=["workflows", "code"],
)
@pytest.mark.parametrize(("one", "other"), [(None, 5), (5, None), (None, None)], ids=["first refuses", "second refuses", "both refuse"])
def test_an_area_with_one_refusing_rule_has_no_extent_whatever_the_other_carries(area, first, second, one, other):
    """REFUSAL-2: an area with one refusing rule has no extent, whatever the other rule carries."""
    verdict = check.AreaVerdict(area, (judging(first, one), judging(second, other)))
    assert cli.area_extent(verdict, 0) == ""


def dependencies_area(extent: LockExtent | None, refusal: CannotJudge | None = None) -> check.AreaVerdict:
    return check.AreaVerdict(Area.DEPENDENCIES, (check.RuleVerdict(Rule.ADVISORY, extent=extent, refusal=refusal),))


@pytest.mark.parametrize("extent", [LockExtent(0, 0), LockExtent(1, 1), LockExtent(2, 63)])
def test_a_judged_dependencies_area_counts_its_lockfiles_and_packages(extent):
    """O48: a judged dependencies area counts its lockfiles, then its packages."""
    assert extent_counts(cli.area_extent(dependencies_area(extent), 0)) == [extent.lockfiles, extent.packages]


def test_a_dependencies_area_that_could_not_check_has_no_extent():
    """REFUSAL-2: a dependencies area that could not check has no extent."""
    assert cli.area_extent(dependencies_area(None, CannotJudge("why")), 0) == ""


def code_area(rules: tuple[Rule, ...], extent: int) -> check.AreaVerdict:
    return check.AreaVerdict(Area.CODE, tuple(judging(rule, extent) for rule in rules))


@pytest.mark.parametrize("rules", [(Rule.PYTHON_SECURITY,), (Rule.PYTHON_SECURITY, Rule.PYTHON_PRINT)], ids=["one rule", "both rules"])
@pytest.mark.parametrize("extent", [0, 1, 41])
def test_a_judged_code_area_counts_its_rules_and_its_python_files(rules, extent):
    """ROW-12: a judged code area counts its rules, then its Python files."""
    assert extent_counts(cli.area_extent(code_area(rules, extent), 0)) == [len(rules), extent]


@pytest.mark.parametrize(
    ("judged", "expected"),
    [
        pytest.param(1, "1 of 1 area could not check", id="one area"),
        pytest.param(2, "1 of 2 areas could not check", id="two areas"),
    ],
)
def test_the_refusal_counts_the_areas_the_run_judged(judged, expected):
    assert cli.refusal_error(areas_refusing((CannotJudge("a"),), *[()] * (judged - 1))) == expected
