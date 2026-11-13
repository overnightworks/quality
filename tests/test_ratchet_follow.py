"""`follow_renames`: the held ceiling as the verdict will carry the committed one."""

import pytest

from quality.baseline_file import ClonePair, JobKey, Key, Metric, PageKey, Place
from quality.ratchet import (
    RESTORE_STEP,
    Renames,
    carried_base,
    follow_renames,
    named_paths,
    raised_entries,
    raised_job_limits,
    raised_literal_texts,
)
from quality.report import CannotJudge, Note, NoteEvent
from tests.support.baselines import ceiling


def sized(path, symbol="f"):
    return Key(Metric.CCN, path, symbol)


def lined(path):
    return Key(Metric.FILE_LINES, path)


def booked(path, job="e2e"):
    return JobKey(path, job)


def paged(entries):
    """A ceiling that holds only page counts, none when `entries` is None."""
    return ceiling({}, None, None, entries)


def timed(entries):
    """A ceiling that holds only job entries, none when `entries` is None."""
    return ceiling({}, None, entries)


def paired(first, second):
    return ClonePair.of(Place(first, "f"), Place(second, "g"))


def renamed(moves, unnamed_sources=()):
    return Renames(moves, frozenset(moves.values()) | frozenset(unnamed_sources))


def tracked_after(held, committed, renames):
    """Every path either ceiling names or a rename targets, but the sources git moved away."""
    return (named_paths(held) | named_paths(committed) | renames.targets) - set(renames.moves)


def moved_entry_cases():
    return [
        pytest.param(
            ceiling({sized("a.py"): 16}, None),
            ceiling({sized("a.py"): 16}, None),
            renamed({"a.py": "b.py"}),
            ceiling({sized("b.py"): 16}, None),
            [NoteEvent.MOVED],
            id="as committed moves",
        ),
        pytest.param(
            ceiling({sized("a.py"): 15}, None),
            ceiling({sized("a.py"): 16}, None),
            renamed({"a.py": "b.py"}),
            ceiling({sized("b.py"): 15}, None),
            [NoteEvent.MOVED],
            id="held lower than committed moves lower",
        ),
        pytest.param(
            ceiling({sized("a.py"): 100, sized("b.py"): 200}, None),
            ceiling({sized("a.py"): 100, sized("b.py"): 200}, None),
            renamed({"a.py": "b.py"}),
            ceiling({sized("b.py"): 100}, None),
            [NoteEvent.MOVED, NoteEvent.DROPPED],
            id="the stale leftover goes",
        ),
        pytest.param(
            ceiling({sized("a.py"): 100, sized("b.py", "g"): 200}, None),
            ceiling({sized("a.py"): 100, sized("b.py", "g"): 200}, None),
            renamed({"a.py": "b.py"}),
            ceiling({sized("b.py"): 100}, None),
            [NoteEvent.MOVED, NoteEvent.DROPPED],
            id="a leftover the source lacks",
        ),
    ]


def followed_entry_cases():
    return [
        pytest.param(
            ceiling({sized("b.py"): 16}, None),
            ceiling({sized("a.py"): 16}, None),
            renamed({"a.py": "b.py"}),
            ceiling({sized("b.py"): 16}, None),
            [],
            id="already followed stays",
        ),
        pytest.param(
            ceiling({sized("b.py"): 15}, None),
            ceiling({sized("a.py"): 16}, None),
            renamed({"a.py": "b.py"}),
            ceiling({sized("b.py"): 15}, None),
            [],
            id="already followed and lowered stays",
        ),
        pytest.param(
            ceiling({}, None),
            ceiling({sized("a.py"): 16}, None),
            renamed({"a.py": "b.py"}),
            ceiling({}, None),
            [],
            id="nothing held stays empty",
        ),
        pytest.param(
            ceiling({sized("b.py"): 200}, None),
            ceiling({sized("b.py"): 200}, None),
            renamed({}, unnamed_sources=["b.py"]),
            ceiling({}, None),
            [NoteEvent.DROPPED],
            id="a source that is not UTF-8 drops the leftover",
        ),
        pytest.param(
            ceiling({sized("a.py"): 16, sized("c.py"): 17}, None),
            ceiling({sized("a.py"): 16, sized("c.py"): 17}, None),
            renamed({"a.py": "b.py"}),
            ceiling({sized("b.py"): 16, sized("c.py"): 17}, None),
            [NoteEvent.MOVED],
            id="an untouched entry stays",
        ),
    ]


def pair_cases():
    return [
        pytest.param(
            ceiling({}, frozenset({paired("a.py", "z.py")})),
            ceiling({}, frozenset({paired("a.py", "z.py")})),
            renamed({"a.py": "b.py"}),
            ceiling({}, frozenset({paired("b.py", "z.py")})),
            [NoteEvent.MOVED],
            id="a pair moves",
        ),
        pytest.param(
            ceiling({}, frozenset({paired("a.py", "z.py"), paired("b.py", "y.py")})),
            ceiling({}, frozenset({paired("a.py", "z.py"), paired("b.py", "y.py")})),
            renamed({"a.py": "b.py"}),
            ceiling({}, frozenset({paired("b.py", "z.py")})),
            [NoteEvent.MOVED, NoteEvent.DROPPED],
            id="a leftover pair goes",
        ),
        pytest.param(
            ceiling({}, frozenset({paired("a.py", "z.py")})),
            ceiling({}, None),
            renamed({"a.py": "b.py"}),
            ceiling({}, frozenset({paired("b.py", "z.py")})),
            [NoteEvent.MOVED],
            id="pairs without a committed table move",
        ),
        pytest.param(
            ceiling({}, frozenset({paired("a.py", "b.py")})),
            ceiling({}, frozenset({paired("a.py", "b.py")})),
            renamed({"a.py": "c.py", "b.py": "d.py"}),
            ceiling({}, frozenset({paired("c.py", "d.py")})),
            [NoteEvent.MOVED],
            id="a pair touching two renames",
        ),
    ]


def job_cases():
    return [
        pytest.param(
            timed({booked("a.yml"): 40}),
            timed({booked("a.yml"): 40}),
            renamed({"a.yml": "b.yml"}),
            timed({booked("b.yml"): 40}),
            [NoteEvent.MOVED],
            id="a job entry as committed moves",
        ),
        pytest.param(
            timed({booked("a.yml"): 30}),
            timed({booked("a.yml"): 40}),
            renamed({"a.yml": "b.yml"}),
            timed({booked("b.yml"): 30}),
            [NoteEvent.MOVED],
            id="a job entry held lower than committed moves lower",
        ),
        pytest.param(
            timed({booked("a.yml"): 40, booked("b.yml"): 25}),
            timed({booked("a.yml"): 40, booked("b.yml"): 25}),
            renamed({"a.yml": "b.yml"}),
            timed({booked("b.yml"): 40}),
            [NoteEvent.MOVED, NoteEvent.DROPPED],
            id="the stale job entry under the target goes",
        ),
        pytest.param(
            timed({booked("a.yml"): 40, booked("a.yml", "lint"): 20}),
            timed({booked("a.yml"): 40, booked("a.yml", "lint"): 20}),
            renamed({"a.yml": "b.yml"}),
            timed({booked("b.yml"): 40, booked("b.yml", "lint"): 20}),
            [NoteEvent.MOVED, NoteEvent.MOVED],
            id="every job of the workflow moves",
        ),
    ]


def job_followed_cases():
    return [
        pytest.param(
            timed({booked("b.yml"): 40}),
            timed({booked("a.yml"): 40}),
            renamed({"a.yml": "b.yml"}),
            timed({booked("b.yml"): 40}),
            [],
            id="a job entry already moved stays",
        ),
        pytest.param(
            timed({booked("b.yml"): 30}),
            timed({booked("a.yml"): 40}),
            renamed({"a.yml": "b.yml"}),
            timed({booked("b.yml"): 30}),
            [],
            id="a job entry already moved and lowered stays",
        ),
    ]


def job_partial_lineage_cases():
    return [
        pytest.param(
            timed({booked("a.yml"): 40}),
            timed(None),
            renamed({"a.yml": "b.yml"}),
            timed({booked("b.yml"): 40}),
            [NoteEvent.MOVED],
            id="job entries without a committed table move",
        ),
        pytest.param(
            timed({booked("b.yml"): 40}),
            timed(None),
            renamed({"a.yml": "b.yml"}),
            timed({booked("b.yml"): 40}),
            [],
            id="job entries without a committed table and already moved stay",
        ),
        pytest.param(
            timed({booked("a.yml"): 40, booked("b.yml"): 10}),
            timed(None),
            renamed({"a.yml": "b.yml"}),
            timed({booked("b.yml"): 40}),
            [NoteEvent.MOVED, NoteEvent.DROPPED],
            id="job entries without a committed table drop the target entry a moved one takes",
        ),
        pytest.param(
            timed({booked("a.yml"): 40, booked("b.yml", "lint"): 20}),
            timed(None),
            renamed({"a.yml": "b.yml"}),
            timed({booked("b.yml"): 40, booked("b.yml", "lint"): 20}),
            [NoteEvent.MOVED],
            id="job entries without a committed table move beside a distinct target entry",
        ),
        pytest.param(
            timed({booked("a.yml"): 40, booked("b.yml", "lint"): 20}),
            timed({booked("a.yml"): 40, booked("a.yml", "lint"): 20}),
            renamed({"a.yml": "b.yml"}),
            timed({booked("b.yml"): 40, booked("b.yml", "lint"): 20}),
            [NoteEvent.MOVED],
            id="a half-moved workflow moves the rest and keeps what is carried",
        ),
        pytest.param(
            timed({booked("a.yml"): 40, booked("b.yml", "lint"): 20}),
            timed({booked("a.yml"): 40, booked("a.yml", "lint"): 20, booked("b.yml", "lint"): 30}),
            renamed({"a.yml": "b.yml"}),
            timed({booked("b.yml"): 40, booked("b.yml", "lint"): 20}),
            [NoteEvent.MOVED],
            id="a half-moved workflow keeps a carried entry whose key the committed target also held",
        ),
    ]


def page_cases():
    return [
        pytest.param(
            paged({PageKey("a.svelte"): 3}),
            paged({PageKey("a.svelte"): 3}),
            renamed({"a.svelte": "b.svelte"}),
            paged({PageKey("b.svelte"): 3}),
            [NoteEvent.MOVED],
            id="a page entry moves with its page",
        ),
        pytest.param(
            paged({PageKey("a.svelte"): 3, PageKey("b.svelte"): 2}),
            paged({PageKey("a.svelte"): 3, PageKey("b.svelte"): 2}),
            renamed({"a.svelte": "b.svelte"}),
            paged({PageKey("b.svelte"): 3}),
            [NoteEvent.MOVED, NoteEvent.DROPPED],
            id="a page entry the target held goes",
        ),
        pytest.param(
            paged(None),
            paged({PageKey("a.svelte"): 3}),
            renamed({"a.svelte": "b.svelte"}),
            paged(None),
            [],
            id="a held ceiling without the page table stays without it",
        ),
        pytest.param(
            paged({}),
            paged({PageKey("a.svelte"): 3}),
            renamed({"a.svelte": "b.svelte"}),
            paged({}),
            [],
            id="a held empty page table stays held",
        ),
        pytest.param(
            ceiling({sized("a.py"): 16}, None, {booked("a.yml"): 40}, {PageKey("a.svelte"): 3}),
            ceiling({sized("a.py"): 16}, None, {booked("a.yml"): 40}, {PageKey("a.svelte"): 3}),
            renamed({"a.py": "b.py", "a.yml": "b.yml", "a.svelte": "b.svelte"}),
            ceiling({sized("b.py"): 16}, None, {booked("b.yml"): 40}, {PageKey("b.svelte"): 3}),
            [NoteEvent.MOVED, NoteEvent.MOVED, NoteEvent.MOVED],
            id="a source file, a workflow and a page move together",
        ),
    ]


def job_table_cases():
    return [
        pytest.param(
            timed({}),
            timed({booked("a.yml"): 40}),
            renamed({"a.yml": "b.yml"}),
            timed({}),
            [],
            id="a held empty job table stays held",
        ),
        pytest.param(
            timed(None),
            timed({booked("a.yml"): 40}),
            renamed({"a.yml": "b.yml"}),
            timed(None),
            [],
            id="a held ceiling without the job table stays without it",
        ),
        pytest.param(
            timed({booked("ci.yml"): 40}),
            timed({booked("ci.yml"): 40}),
            renamed({"a.yml": "b.yml"}),
            timed({booked("ci.yml"): 40}),
            [],
            id="a workflow no rename touches is handed on",
        ),
        pytest.param(
            ceiling({sized("a.py"): 16}, None, {booked("a.yml"): 40}),
            ceiling({sized("a.py"): 16}, None, {booked("a.yml"): 40}),
            renamed({"a.py": "b.py", "a.yml": "b.yml"}),
            ceiling({sized("b.py"): 16}, None, {booked("b.yml"): 40}),
            [NoteEvent.MOVED, NoteEvent.MOVED],
            id="a source file and a workflow move together",
        ),
    ]


def follow_cases():
    return pytest.mark.parametrize(
        ("held", "committed", "renames", "expected", "events"),
        [*moved_entry_cases(), *followed_entry_cases(), *pair_cases(), *job_cases(), *job_followed_cases(), *job_partial_lineage_cases(), *job_table_cases(), *page_cases()],
    )


@follow_cases()
def test_follow_renames(held, committed, renames, expected, events):
    """FIX-36, FIX-37, FIX-38, TIMEOUT-19: an entry, a pair or a job entry moves with its file at its held value, what
    the target held goes, and an entry already moved stays."""
    followed = follow_renames(held, committed, renames, tracked_after(held, committed, renames))
    assert (followed.ceiling, [change.event for change in followed.changes]) == (expected, events)


@follow_cases()
def test_the_verdict_names_nothing_the_fix_carried(held, committed, renames, expected, events):
    """FIX-34, TIMEOUT-19: the verdict, reading the committed ceiling as renamed, names nothing the fix carried."""
    result = follow_renames(held, committed, renames, tracked_after(held, committed, renames)).ceiling
    carried = carried_base(committed, renames, named_paths(result))
    assert raised_entries(carried.size, result.size) == []
    tables = (carried.job_minutes, result.job_minutes)
    assert None in tables or raised_job_limits(*tables) == []
    pages = (carried.literal_texts, result.literal_texts)
    assert None in pages or raised_literal_texts(*pages) == []


def test_a_followed_job_entry_is_named_by_its_new_path_in_the_file_order_with_the_leftover_last():
    """TIMEOUT-19: a moved job entry's note carries the new path, source entries come first in the file's order, and
    the leftover under the target is dropped last."""
    held = timed({booked("b.yml", "old"): 25, booked("a.yml", "lint"): 20, booked("a.yml"): 40})
    renames = renamed({"a.yml": "b.yml"})
    followed = follow_renames(held, held, renames, tracked_after(held, held, renames))
    assert followed.changes == (
        Note("b.yml", NoteEvent.MOVED, "job lint 20 minutes from a.yml"),
        Note("b.yml", NoteEvent.MOVED, "job e2e 40 minutes from a.yml"),
        Note("b.yml", NoteEvent.DROPPED, "job old 25 minutes · left from before the move"),
    )


@pytest.mark.parametrize(
    ("held", "committed", "tracked", "path"),
    [
        pytest.param(
            ceiling({sized("b.py", "g"): 5}, None),
            ceiling({sized("a.py"): 100}, None),
            {"b.py"},
            "b.py",
            id="neither form",
        ),
        pytest.param(
            ceiling({sized("a.py"): 150}, None),
            ceiling({sized("a.py"): 100}, None),
            {"b.py"},
            "a.py",
            id="a hand raise under a renamed file",
        ),
        pytest.param(
            ceiling({sized("b.py"): 16, sized("z.py"): 30}, None),
            ceiling({sized("a.py"): 16}, None),
            {"b.py"},
            "z.py",
            id="untracked and not committed",
        ),
        pytest.param(
            ceiling({lined("b.py"): 900, sized("a.py"): 20}, None),
            ceiling({lined("a.py"): 900, sized("a.py"): 20}, None),
            {"b.py"},
            "b.py",
            id="size entries half moved by hand are one group",
        ),
        pytest.param(
            ceiling({sized("a.py"): 16, sized("b.py", "g"): 17}, None),
            ceiling({sized("a.py"): 16, sized("a.py", "g"): 17}, None),
            {"b.py"},
            "b.py",
            id="size entries neither committed nor carried as a group",
        ),
        pytest.param(
            timed({booked("b.yml", "lint"): 5}),
            timed({booked("a.yml"): 40}),
            {"b.yml"},
            "b.yml",
            id="a job entry neither committed nor under the old path",
        ),
        pytest.param(
            timed({booked("a.yml"): 45}),
            timed({booked("a.yml"): 40}),
            {"b.yml"},
            "a.yml",
            id="a hand raise of a job entry under a renamed workflow",
        ),
        pytest.param(
            timed({booked("b.yml"): 40, booked("z.yml"): 30}),
            timed({booked("a.yml"): 40}),
            {"b.yml"},
            "z.yml",
            id="a job entry untracked and not committed",
        ),
    ],
)
def test_an_entry_without_lineage_refuses(held, committed, tracked, path):
    """FIX-39, TIMEOUT-19: an entry that neither the committed ceiling nor the tracked paths explain refuses, naming its
    path (a workflow's, when a job entry is the cause) and the restore step."""
    renames, still_tracked = renamed({"a.py": "b.py", "a.yml": "b.yml"}), frozenset(tracked)
    with pytest.raises(CannotJudge) as refusal:
        follow_renames(held, committed, renames, still_tracked)
    fact = refusal.value.reason.fact
    assert (
        {name for name in ("a.py", "b.py", "z.py", "a.yml", "b.yml", "z.yml") if name in fact},
        refusal.value.reason.next_steps,
    ) == (
        {path},
        (RESTORE_STEP,),
    )
