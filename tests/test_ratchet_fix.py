"""The ceiling `--fix` builds: what falls, what goes, what stays and what never enters."""

import pytest

from quality.ratchet import (
    Baseline,
    ClonePair,
    Key,
    Measured,
    Metric,
    Place,
    SizeError,
    holds_clones_table,
    lower_ceiling,
)
from quality.report import Note, NoteEvent

FILE = Key(Metric.FILE_LINES, "src/a.py")
FUNCTION = Key(Metric.FUNCTION_LINES, "src/a.py", "work")
COMPLEXITY = Key(Metric.CCN, "src/a.py", "work")
PAIR = ClonePair.of(Place("src/a.py", "work"), Place("src/b.py", "work"))
OTHER_PAIR = ClonePair.of(Place("src/a.py", "rest"), Place("src/c.py", "rest"))


def fixed(held: dict[Key, int], measured: dict[Key, int | None], pairs=frozenset(), held_pairs=frozenset()):
    measurements = {key: Measured(value, 3) for key, value in measured.items() if value is not None}
    return lower_ceiling(Baseline(held, held_pairs), measurements, pairs)


@pytest.mark.parametrize(
    ("key", "held", "measured", "expected"),
    [
        pytest.param(FILE, 826, 812, 812, id="fell"),
        pytest.param(FILE, 826, 826, 826, id="equal"),
        pytest.param(FILE, 826, 900, 826, id="grew"),
        pytest.param(FILE, 826, None, 826, id="gone"),
        pytest.param(FILE, 826, 790, None, id="below the threshold"),
        pytest.param(FILE, 826, 0, None, id="zero"),
        pytest.param(COMPLEXITY, 19, 17, 17, id="ccn"),
        pytest.param(COMPLEXITY, 17, 15, None, id="ccn below the threshold"),
        pytest.param(FUNCTION, 70, 62, 62, id="a function"),
    ],
)
def test_each_held_entry_falls_goes_or_stays(key, held, measured, expected):
    entries = fixed({key: held}, {key: measured}, held_pairs=frozenset()).ceiling.size
    assert entries == ({} if expected is None else {key: expected})


@pytest.mark.parametrize("value", [799, 800, 801])
def test_no_entry_ever_rises(value):
    entries = fixed({FILE: 800}, {FILE: value}).ceiling.size
    assert entries.get(FILE, 0) <= 800


def test_a_new_key_never_enters():
    entries = fixed({FILE: 826}, {FILE: 812, FUNCTION: 90}).ceiling.size
    assert entries == {FILE: 812}


def test_a_pair_the_tree_lost_goes_and_a_new_one_stays_out():
    fix = fixed({}, {}, pairs=frozenset({OTHER_PAIR}), held_pairs=frozenset({PAIR}))
    assert fix.ceiling.clones == frozenset()
    assert Note("src/a.py", NoteEvent.DROPPED, "pair src/a.py work <-> src/b.py work is gone") in fix.changes


def test_a_file_without_clones_records_its_pairs():
    fix = lower_ceiling(Baseline({}, None), {}, frozenset({PAIR}))
    assert fix.ceiling.clones == frozenset({PAIR})
    assert [change.event for change in fix.changes] == [NoteEvent.RECORDED]


def test_a_lowered_entry_is_noted_with_its_fall_and_a_dropped_one_with_its_threshold():
    fix = fixed({FILE: 826, COMPLEXITY: 19}, {FILE: 790, COMPLEXITY: 17}, held_pairs=frozenset())
    assert [(note.event, note.fact) for note in fix.changes] == [
        (NoteEvent.LOWERED, "function work CCN 19 → 17"),
        (NoteEvent.DROPPED, "file 826 → 790 lines · debt from 800 lines"),
    ]


@pytest.mark.parametrize(
    ("version", "held"),
    [
        pytest.param(b"[clones]\n", True, id="[clones]"),
        pytest.param(b"[ clones ]\n", True, id="[ clones ]"),
        pytest.param(b"clones = {}\n", True, id="clones = {}"),
        pytest.param(b'["clones"]\n', True, id='["clones"]'),
        pytest.param(b'[function_lines]\nclones = {}\n', False, id="a function named clones"),
        pytest.param(b'[file_lines]\n"clones" = 120\n', False, id="a file named clones"),
    ],
)
def test_a_version_holds_clones_if_it_parses_so(version, held):
    assert holds_clones_table(version) is held


@pytest.mark.parametrize("version", [b"[clones", b"\xff\xfe"], ids=["[clones", "not UTF-8"])
def test_a_version_no_parser_reads_shows_nothing(version):
    with pytest.raises(SizeError, match="a version of quality/baseline.toml in git cannot be read"):
        holds_clones_table(version)
