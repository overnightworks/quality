"""`follow_renames`: the held ceiling as the verdict will carry the committed one."""

import pytest

from quality.ratchet import (
    RESTORE_STEP,
    Baseline,
    ClonePair,
    Key,
    Metric,
    Place,
    Renames,
    carried_base,
    follow_renames,
    named_paths,
    raised_entries,
)
from quality.report import CannotJudge, NoteEvent


def sized(path, symbol="f"):
    return Key(Metric.CCN, path, symbol)


def paired(first, second):
    return ClonePair.of(Place(first, "f"), Place(second, "g"))


def renamed(moves, unnamed_sources=()):
    return Renames(moves, frozenset(moves.values()) | frozenset(unnamed_sources))


def tracked_after(held, committed, renames):
    """Every path either ceiling names, but the sources git moved away."""
    paths = named_paths(held) | named_paths(committed)
    return paths - set(renames.moves)


def follow_cases():
    return pytest.mark.parametrize(
        ("held", "committed", "renames", "expected", "events"),
        [
            pytest.param(
                Baseline({sized("a.py"): 16}, None),
                Baseline({sized("a.py"): 16}, None),
                renamed({"a.py": "b.py"}),
                Baseline({sized("b.py"): 16}, None),
                [NoteEvent.MOVED],
                id="as committed moves",
            ),
            pytest.param(
                Baseline({sized("a.py"): 15}, None),
                Baseline({sized("a.py"): 16}, None),
                renamed({"a.py": "b.py"}),
                Baseline({sized("b.py"): 15}, None),
                [NoteEvent.MOVED],
                id="held lower than committed moves lower",
            ),
            pytest.param(
                Baseline({sized("a.py"): 100, sized("b.py"): 200}, None),
                Baseline({sized("a.py"): 100, sized("b.py"): 200}, None),
                renamed({"a.py": "b.py"}),
                Baseline({sized("b.py"): 100}, None),
                [NoteEvent.MOVED, NoteEvent.DROPPED],
                id="the stale leftover goes",
            ),
            pytest.param(
                Baseline({sized("a.py"): 100, sized("b.py", "g"): 200}, None),
                Baseline({sized("a.py"): 100, sized("b.py", "g"): 200}, None),
                renamed({"a.py": "b.py"}),
                Baseline({sized("b.py"): 100}, None),
                [NoteEvent.MOVED, NoteEvent.DROPPED],
                id="a leftover the source lacks",
            ),
            pytest.param(
                Baseline({sized("b.py"): 16}, None),
                Baseline({sized("a.py"): 16}, None),
                renamed({"a.py": "b.py"}),
                Baseline({sized("b.py"): 16}, None),
                [],
                id="already followed stays",
            ),
            pytest.param(
                Baseline({sized("b.py"): 15}, None),
                Baseline({sized("a.py"): 16}, None),
                renamed({"a.py": "b.py"}),
                Baseline({sized("b.py"): 15}, None),
                [],
                id="already followed and lowered stays",
            ),
            pytest.param(
                Baseline({}, None),
                Baseline({sized("a.py"): 16}, None),
                renamed({"a.py": "b.py"}),
                Baseline({}, None),
                [],
                id="nothing held stays empty",
            ),
            pytest.param(
                Baseline({sized("b.py"): 200}, None),
                Baseline({sized("b.py"): 200}, None),
                renamed({}, unnamed_sources=["b.py"]),
                Baseline({}, None),
                [NoteEvent.DROPPED],
                id="a source that is not UTF-8 drops the leftover",
            ),
            pytest.param(
                Baseline({sized("a.py"): 16, sized("c.py"): 17}, None),
                Baseline({sized("a.py"): 16, sized("c.py"): 17}, None),
                renamed({"a.py": "b.py"}),
                Baseline({sized("b.py"): 16, sized("c.py"): 17}, None),
                [NoteEvent.MOVED],
                id="an untouched entry stays",
            ),
            pytest.param(
                Baseline({}, frozenset({paired("a.py", "z.py")})),
                Baseline({}, frozenset({paired("a.py", "z.py")})),
                renamed({"a.py": "b.py"}),
                Baseline({}, frozenset({paired("b.py", "z.py")})),
                [NoteEvent.MOVED],
                id="a pair moves",
            ),
            pytest.param(
                Baseline({}, frozenset({paired("a.py", "z.py"), paired("b.py", "y.py")})),
                Baseline({}, frozenset({paired("a.py", "z.py"), paired("b.py", "y.py")})),
                renamed({"a.py": "b.py"}),
                Baseline({}, frozenset({paired("b.py", "z.py")})),
                [NoteEvent.MOVED, NoteEvent.DROPPED],
                id="a leftover pair goes",
            ),
            pytest.param(
                Baseline({}, frozenset({paired("a.py", "z.py")})),
                Baseline({}, None),
                renamed({"a.py": "b.py"}),
                Baseline({}, frozenset({paired("b.py", "z.py")})),
                [NoteEvent.MOVED],
                id="pairs without a committed table move",
            ),
            pytest.param(
                Baseline({}, frozenset({paired("a.py", "b.py")})),
                Baseline({}, frozenset({paired("a.py", "b.py")})),
                renamed({"a.py": "c.py", "b.py": "d.py"}),
                Baseline({}, frozenset({paired("c.py", "d.py")})),
                [NoteEvent.MOVED],
                id="a pair touching two renames",
            ),
        ],
    )


@follow_cases()
def test_follow_renames(held, committed, renames, expected, events):
    followed = follow_renames(held, committed, renames, tracked_after(held, committed, renames))
    assert (followed.ceiling, [change.event for change in followed.changes]) == (expected, events)


@follow_cases()
def test_the_verdict_names_nothing_the_fix_carried(held, committed, renames, expected, events):
    result = follow_renames(held, committed, renames, tracked_after(held, committed, renames)).ceiling
    carried = carried_base(committed, renames, named_paths(result))
    assert raised_entries(carried.size, result.size) == []


@pytest.mark.parametrize(
    ("held", "committed", "tracked", "path"),
    [
        pytest.param(
            Baseline({sized("b.py", "g"): 5}, None),
            Baseline({sized("a.py"): 100}, None),
            {"b.py"},
            "b.py",
            id="neither form",
        ),
        pytest.param(
            Baseline({sized("a.py"): 150}, None),
            Baseline({sized("a.py"): 100}, None),
            {"b.py"},
            "a.py",
            id="a hand raise under a renamed file",
        ),
        pytest.param(
            Baseline({sized("b.py"): 16, sized("z.py"): 30}, None),
            Baseline({sized("a.py"): 16}, None),
            {"b.py"},
            "z.py",
            id="untracked and not committed",
        ),
    ],
)
def test_an_entry_without_lineage_refuses(held, committed, tracked, path):
    with pytest.raises(CannotJudge) as refusal:
        follow_renames(held, committed, renamed({"a.py": "b.py"}), frozenset(tracked))
    fact = refusal.value.reason.fact
    assert ({name for name in ("a.py", "b.py", "z.py") if name in fact}, refusal.value.reason.next_steps) == (
        {path},
        (RESTORE_STEP,),
    )
