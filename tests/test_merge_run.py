"""`quality merge` with several names, driven in this process against a fake child port and a fake terminal."""

import dataclasses

import pytest

from quality.process import Finished
from tests.support.merge import FakeGh, closing_line, door_ports, drive, restore_line
from tests.support.merge_run import (
    FIRST,
    SECOND,
    SIGKILL_RETURNCODE,
    THIRD,
    FakeChildren,
    command_of,
    judging,
    merge_names,
    merge_names_interrupted,
    row,
)

SIGINT_RETURNCODE = -2
WIDTH = 14


def summary(glyph: str, name: str, event: str, fact: str = "") -> str:
    return row(glyph, name, event, fact, width=WIDTH)


@pytest.mark.parametrize(
    ("words", "expected"),
    [
        pytest.param(
            (THIRD, FIRST, SECOND, "--dry-run", "-v"),
            [command_of(name, "--dry-run", "-v") for name in (THIRD, FIRST, SECOND)],
            id="a child per name in the order named, each with the flags given and the working directory off its path",
        ),
        pytest.param((FIRST, SECOND), [command_of(FIRST), command_of(SECOND)], id="no flag where none was given"),
    ],
)
def test_each_name_runs_one_child_in_the_order_named_with_its_flags(words, expected, capsys):
    """MERGE-108, MERGE-109: a child per name, in the order named, each the running interpreter with `-P` and its own
    name alone, `--dry-run` and `-v` only where the run was given them."""
    code, _, _, children = merge_names(capsys, *words)
    assert (code, children.commands) == (0, expected)


def test_a_row_names_each_pull_request_and_its_place_before_its_child(capsys):
    """MERGE-110: before each child one row names the pull request and its place in the run."""
    _, out, _, _ = merge_names(capsys, FIRST, SECOND, THIRD)
    assert out.startswith(judging(1, 3, FIRST))
    assert judging(2, 3, SECOND) in out
    assert judging(3, 3, THIRD) in out


def test_the_next_child_starts_after_the_previous_one_exited_and_the_terminal_was_cleared(capsys):
    """MERGE-108, MERGE-110: each child starts after the one before exited, also for two of one repository, and pending
    terminal input is discarded before each."""
    _, _, _, children = merge_names(capsys, FIRST, SECOND, THIRD)
    assert children.events == [
        "discard input", f"start {FIRST}", f"exit {FIRST}",
        "discard input", f"start {SECOND}", f"exit {SECOND}",
        "discard input", f"start {THIRD}", f"exit {THIRD}",
    ]


def test_all_merged_ends_with_the_count_and_exit_0(capsys):
    """MERGE-111: every child exits 0, a row per name says merged and the closing line counts them."""
    code, out, err, _ = merge_names(capsys, FIRST, SECOND)
    assert (code, err) == (0, "")
    assert out.endswith(f"{summary('✓', FIRST, 'merged')}{summary('✓', SECOND, 'merged')}✓ merged 2 of 2 · <1s\n")


def test_a_child_that_exits_1_does_not_stop_the_run(capsys):
    """MERGE-111: a refused pull request or a `no` leaves the rest running; the run exits 1 and the pair names those not
    merged as the command to run again."""
    code, out, err, children = merge_names(capsys, FIRST, SECOND, THIRD, endings={SECOND: 1})
    assert (code, children.names) == (1, [FIRST, SECOND, THIRD])
    assert out.endswith(
        summary("✓", FIRST, "merged") + summary("!", SECOND, "not merged") + summary("✓", THIRD, "merged")
    )
    assert err == f"error: merged 2 of 3 · 1 not merged · nothing changed\nnext: quality merge {SECOND}\n"


def test_a_child_that_exits_2_stops_the_run_and_the_pair_names_the_step_above_then_the_rest(capsys):
    """MERGE-112: after exit 2 no child starts, the rest show as not run and `next:` names the child's own step first,
    then `quality merge` with the names not run."""
    code, out, err, children = merge_names(capsys, FIRST, SECOND, THIRD, endings={FIRST: 2})
    assert (code, children.names) == (2, [FIRST])
    assert out.endswith(summary("✗", FIRST, "stopped") + summary("·", SECOND, "not run") + summary("·", THIRD, "not run"))
    assert err == (
        f"error: stopped at {FIRST} · merged 0 of 3 · 2 not run\n"
        f"next:\n    do the step named above for {FIRST} at once\n    quality merge {SECOND} {THIRD}\n"
    )


def test_a_dry_run_names_the_dry_run_in_the_rest_it_offers(capsys):
    """MERGE-112: the command for the names not run carries `--dry-run` where the run had it."""
    _, _, err, _ = merge_names(capsys, FIRST, SECOND, "--dry-run", endings={FIRST: 2})
    assert err.endswith(f"    quality merge {SECOND} --dry-run\n")


def test_the_last_child_that_exits_2_leaves_the_step_above_alone_in_the_pair(capsys):
    """MERGE-112: with no name left, `next:` is the child's step alone."""
    code, _, err, _ = merge_names(capsys, FIRST, SECOND, endings={SECOND: 2})
    assert (code, err) == (
        2,
        f"error: stopped at {SECOND} · merged 1 of 2\nnext: do the step named above for {SECOND} at once\n",
    )


def test_a_dry_run_ends_with_the_dry_run_line_and_exit_0(capsys):
    """MERGE-117: a dry run that every child ended with 0 shows would merge per name and one dry run line, nothing changed."""
    code, out, err, _ = merge_names(capsys, FIRST, SECOND, "--dry-run")
    assert (code, err) == (0, "")
    assert out.endswith(
        f"{summary('~', FIRST, 'would merge')}{summary('~', SECOND, 'would merge')}~ dry run · would merge 2 of 2 · nothing changed\n"
    )


def test_a_dry_run_with_a_refusal_names_what_would_merge(capsys):
    """MERGE-117: a dry run that a child refused ends with the pair, worded as would merge."""
    code, _, err, _ = merge_names(capsys, FIRST, SECOND, "--dry-run", endings={FIRST: 1})
    assert (code, err) == (1, f"error: would merge 1 of 2 · 1 not merged · nothing changed\nnext: quality merge {FIRST} --dry-run\n")


def test_without_a_terminal_no_child_starts_and_the_pair_stands_alone(capsys):
    """MERGE-115: no terminal on stdin is the pair alone, exit 1, and no child starts."""
    code, out, err, children = merge_names(capsys, FIRST, SECOND, terminal=False)
    assert (code, out, children.commands) == (1, "", [])
    assert err == (
        "error: not merged · the operator's merge needs a terminal on stdin · nothing changed\n"
        "next: the operator runs it in his own terminal\n"
    )


def test_a_word_that_names_no_pull_request_is_refused_before_any_child(capsys):
    """MERGE-116: a word among several names that is no name is refused naming that word, and no child starts."""
    code, out, err, children = merge_names(capsys, FIRST, "widgets#7", SECOND)
    assert (code, out, children.commands) == (2, "", [])
    assert err == "error: no pull request widgets#7 · a pull request reads OWNER/REPO#N\nnext: quality merge --help\n"


def test_one_name_runs_the_door_in_this_process(capsys):
    """MERGE-117: one name starts no child: the door runs in the same process and prints as it does for one."""
    children = FakeChildren()
    ports = dataclasses.replace(door_ports(), start_child=children)
    code, _ = drive(FakeGh(merge_lands=True), ports=ports)
    assert (code, children.commands, capsys.readouterr().out.endswith(closing_line())) == (0, [], True)


@pytest.mark.parametrize(
    ("ending", "glyph", "event"),
    [
        pytest.param(Finished(0, interrupted=True), "✓", "merged", id="a child that merged and exited 0"),
        pytest.param(Finished(1, interrupted=True), "!", "not merged", id="a child that exited 1"),
        pytest.param(Finished(SIGINT_RETURNCODE, interrupted=True), "!", "interrupted", id="a child that ended by the interrupt"),
        pytest.param(Finished(130, interrupted=True), "!", "interrupted", id="a child that exited 130"),
    ],
)
def test_an_interrupted_run_exits_130_never_0_or_1_and_starts_nothing_more(ending, glyph, event, capsys):
    """MERGE-113, MERGE-118: an interruption ends the run 130 whatever the interrupted child's own exit was, the rest show
    as not run and no pair stands after the rows."""
    code, out, err, children = merge_names(capsys, FIRST, SECOND, endings={FIRST: ending})
    assert (code, err, children.names) == (130, "", [FIRST])
    assert out.endswith(summary(glyph, FIRST, event) + summary("·", SECOND, "not run"))


def test_an_interrupted_child_that_exits_2_ends_the_run_2_and_the_pair_names_its_step_first(capsys):
    """MERGE-113, MERGE-118: 2 beats 130: the pair names that pull request's step first, then the rest."""
    code, _, err, children = merge_names(capsys, FIRST, SECOND, endings={FIRST: Finished(2, interrupted=True)})
    assert (code, children.names) == (2, [FIRST])
    assert err == (
        f"error: stopped at {FIRST} · merged 0 of 2 · 1 not run\n"
        f"next:\n    do the step named above for {FIRST} at once\n    quality merge {SECOND}\n"
    )


def test_a_child_killed_by_a_signal_leaves_the_gate_possibly_off_and_the_pair_names_the_restore_first(capsys):
    """MERGE-114: a child killed by a signal other than SIGINT printed no pair: the run says the gate may still be off on
    its repository, names the restore command first, then the rest, exits 2 and starts no child."""
    code, out, err, children = merge_names(capsys, FIRST, SECOND, THIRD, endings={FIRST: SIGKILL_RETURNCODE})
    assert (code, children.names) == (2, [FIRST])
    assert out.endswith(summary("✗", FIRST, "killed", "signal 9") + summary("·", SECOND, "not run") + summary("·", THIRD, "not run"))
    assert err == (
        f"error: quality_gate MAY STILL BE OFF on acme/widgets · {FIRST} was killed by signal 9 · merged 0 of 3 · 2 not run\n"
        f"next:\n{restore_line()}    quality merge {SECOND} {THIRD}\n"
    )


@pytest.mark.parametrize(
    ("endings", "code"),
    [
        pytest.param({FIRST: 1, SECOND: Finished(0, interrupted=True)}, 130, id="refused, then interrupted"),
        pytest.param({FIRST: 1, SECOND: Finished(2, interrupted=True)}, 2, id="refused, then interrupted and stopped"),
        pytest.param({FIRST: 1, SECOND: SIGKILL_RETURNCODE}, 2, id="refused, then killed"),
    ],
)
def test_the_run_exits_with_the_worst_ending_2_then_130_then_1_then_0(endings, code, capsys):
    """MERGE-118: the run's exit is 2, else 130, else 1, else 0."""
    assert merge_names(capsys, FIRST, SECOND, endings=endings)[0] == code


STOPPED_PAIR = (
    f"error: stopped at {FIRST} · merged 0 of 2 · 1 not run\n"
    f"next:\n    do the step named above for {FIRST} at once\n    quality merge {SECOND}\n"
)
KILLED_PAIR = (
    f"error: quality_gate MAY STILL BE OFF on acme/widgets · {FIRST} was killed by signal 9 · merged 0 of 2 · 1 not run\n"
    f"next:\n{restore_line()}    quality merge {SECOND}\n"
)


@pytest.mark.parametrize(
    ("markers", "endings", "code", "started", "err"),
    [
        pytest.param({"out_marker": "2 of 2"}, {}, 130, [FIRST], "", id="while the row of the next pull request is printed, no child starts"),
        pytest.param({"out_marker": "merged"}, {}, 130, [FIRST, SECOND], "", id="while the summary of merged pull requests is printed, the run ends 130 and not 0"),
        pytest.param({"out_marker": "stopped"}, {FIRST: 2}, 2, [FIRST], STOPPED_PAIR, id="while the summary of a stop is printed, the pair follows whole"),
        pytest.param({"err_marker": "error:"}, {FIRST: 2}, 2, [FIRST], STOPPED_PAIR, id="while the pair of a stop is printed, it ends whole"),
        pytest.param({"err_marker": "error:"}, {FIRST: SIGKILL_RETURNCODE}, 2, [FIRST], KILLED_PAIR, id="while the pair of a killed child is printed, its restore step is printed"),
        pytest.param(
            {"err_marker": "error:"}, {FIRST: 1}, 130, [FIRST, SECOND],
            f"error: merged 1 of 2 · 1 not merged · nothing changed\nnext: quality merge {FIRST}\n",
            id="while the pair of a refusal is printed, it ends whole and the run ends 130 and not 1",
        ),
    ],
)
def test_an_interrupt_while_the_run_prints_loses_nothing_and_the_exit_follows_the_precedence(markers, endings, code, started, err):
    """MERGE-113, MERGE-118: a Ctrl-C that arrives outside the wait, while the run prints a row, the summary or the pair,
    starts no further child, still prints the summary and the pair, and ends with the exit by the precedence."""
    result, out, printed, children = merge_names_interrupted(FIRST, SECOND, endings=endings, **markers)
    assert (result, children.names, printed) == (code, started, err)
    assert len([line for line in out.splitlines() if "judging" not in line]) == 2
