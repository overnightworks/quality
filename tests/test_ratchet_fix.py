"""The ceiling `--fix` builds: what falls, what goes, what stays and what never enters."""

import pytest

from quality.baseline_file import (
    Baseline,
    ClonePair,
    Key,
    Metric,
    Place,
    load_baseline,
    render_baseline,
)
from quality.ratchet import Measured, lower_ceiling
from quality.report import Note, NoteEvent


def size_key(metric: Metric, symbol: str = "") -> Key:
    return Key(metric, "src/a.py", symbol)


def pair(first: str, second: str) -> ClonePair:
    return ClonePair.of(Place("src/a.py", first), Place(f"src/{second}.py", first))


def fixed(held: dict[Key, int], measured: dict[Key, int | None], pairs=frozenset(), held_pairs=frozenset(), gone=None):
    measurements = {key: Measured(value, 3) for key, value in measured.items() if value is not None}
    return lower_ceiling(Baseline(held, held_pairs), measurements, pairs, gone or {})


@pytest.mark.parametrize(
    ("key", "held", "measured", "expected"),
    [
        pytest.param(size_key(Metric.FILE_LINES), 826, 812, 812, id="fell"),
        pytest.param(size_key(Metric.FILE_LINES), 826, 826, 826, id="equal"),
        pytest.param(size_key(Metric.FILE_LINES), 826, 900, 826, id="grew"),
        pytest.param(size_key(Metric.FILE_LINES), 826, None, 826, id="no measurement, not gone, stands"),
        pytest.param(size_key(Metric.FILE_LINES), 826, 790, None, id="below the threshold"),
        pytest.param(size_key(Metric.FILE_LINES), 826, 0, None, id="zero"),
        pytest.param(size_key(Metric.CCN, "work"), 19, 17, 17, id="ccn"),
        pytest.param(size_key(Metric.CCN, "work"), 17, 15, None, id="ccn below the threshold"),
        pytest.param(size_key(Metric.FUNCTION_LINES, "work"), 70, 62, 62, id="a function"),
    ],
)
def test_each_held_entry_falls_goes_or_stays(key, held, measured, expected):
    """FIX-3, FIX-4, FIX-5, CEILING-8: an entry falls to its measured value, goes below its threshold, stays where it
    grew and stands where nothing measures it."""
    entries = fixed({key: held}, {key: measured}, held_pairs=frozenset()).ceiling.size
    assert entries == ({} if expected is None else {key: expected})


@pytest.mark.parametrize(
    ("key", "held"),
    [
        pytest.param(size_key(Metric.FILE_LINES), 826, id="a file"),
        pytest.param(size_key(Metric.FUNCTION_LINES, "work"), 70, id="a function"),
        pytest.param(size_key(Metric.CCN, "work"), 19, id="ccn"),
    ],
)
@pytest.mark.parametrize(("offset", "written_offset"), [(-1, -1), (0, 0), (1, 0)], ids=["one below", "at it", "one above"])
def test_no_entry_ever_rises(key, held, offset, written_offset):
    """FIX-3, FIX-5: an entry measured one below its held value is written one below, at it stays, and above it stays at
    its held value, in the ceiling and in the TOML it renders."""
    fix = fixed({key: held}, {key: held + offset})
    expected = {key: held + written_offset}
    assert (fix.ceiling.size, load_baseline(render_baseline(fix.ceiling)).size) == (expected, expected)


def test_a_gone_entry_is_dropped_and_noted_with_its_reason():
    """FIX-8: an entry `check` calls gone goes, noted `dropped` with the reason `check` gave."""
    file = size_key(Metric.FILE_LINES)
    fix = fixed({file: 826}, {}, gone={file: "file is gone"})
    assert (fix.ceiling.size, fix.changes) == ({}, (Note("src/a.py", NoteEvent.DROPPED, "file is gone"),))


def test_a_dropped_note_follows_the_lowered_ones_and_precedes_the_pairs():
    """FIX-3, FIX-8, FIX-9: a lowered entry, a gone entry and a gone pair each leave a note, in that order."""
    file, function = size_key(Metric.FILE_LINES), size_key(Metric.FUNCTION_LINES, "work")
    fix = fixed({file: 826, function: 90}, {function: 62}, held_pairs=frozenset({pair("work", "b")}), gone={file: "file is gone"})
    assert [(note.event, note.fact) for note in fix.changes] == [
        (NoteEvent.LOWERED, "function work 90 → 62 lines"),
        (NoteEvent.DROPPED, "file is gone"),
        (NoteEvent.DROPPED, "pair src/a.py work <-> src/b.py work is gone"),
    ]


def test_a_new_key_never_enters():
    """FIX-6: an entry the ceiling does not hold stays out of it, however far past its threshold."""
    file, function = size_key(Metric.FILE_LINES), size_key(Metric.FUNCTION_LINES, "work")
    assert fixed({file: 826}, {file: 812, function: 90}).ceiling.size == {file: 812}


def test_a_pair_the_tree_lost_goes_and_a_new_one_stays_out():
    """FIX-9, FIX-10: a held pair the tree lost goes with a note, and a pair the tree gained stays out."""
    fix = fixed({}, {}, pairs=frozenset({pair("rest", "c")}), held_pairs=frozenset({pair("work", "b")}))
    assert fix.ceiling.clones == frozenset()
    assert Note("src/a.py", NoteEvent.DROPPED, "pair src/a.py work <-> src/b.py work is gone") in fix.changes


def test_a_file_without_clones_records_its_pairs():
    """FIX-15: a file without `[clones]` records its pairs and notes the recording."""
    fix = lower_ceiling(Baseline({}, None), {}, frozenset({pair("work", "b")}), {})
    assert fix.ceiling.clones == frozenset({pair("work", "b")})
    assert [change.event for change in fix.changes] == [NoteEvent.RECORDED]


def test_a_lowered_entry_is_noted_with_its_fall_and_a_dropped_one_with_its_threshold():
    """FIX-3, FIX-4: a lowered entry is noted with its fall and a dropped one with its fall and its threshold."""
    file, complexity = size_key(Metric.FILE_LINES), size_key(Metric.CCN, "work")
    fix = fixed({file: 826, complexity: 19}, {file: 790, complexity: 17}, held_pairs=frozenset())
    assert [(note.event, note.fact) for note in fix.changes] == [
        (NoteEvent.LOWERED, "function work CCN 19 → 17"),
        (NoteEvent.DROPPED, "file 826 → 790 lines · debt from 800 lines"),
    ]
