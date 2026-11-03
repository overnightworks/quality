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
    load_baseline,
    lower_ceiling,
    render_baseline,
)
from quality.report import Note, NoteEvent


def size_key(metric: Metric, symbol: str = "") -> Key:
    return Key(metric, "src/a.py", symbol)


def pair(first: str, second: str) -> ClonePair:
    return ClonePair.of(Place("src/a.py", first), Place(f"src/{second}.py", first))


def fixed(held: dict[Key, int], measured: dict[Key, int | None], pairs=frozenset(), held_pairs=frozenset()):
    measurements = {key: Measured(value, 3) for key, value in measured.items() if value is not None}
    return lower_ceiling(Baseline(held, held_pairs), measurements, pairs)


@pytest.mark.parametrize(
    ("key", "held", "measured", "expected"),
    [
        pytest.param(size_key(Metric.FILE_LINES), 826, 812, 812, id="fell"),
        pytest.param(size_key(Metric.FILE_LINES), 826, 826, 826, id="equal"),
        pytest.param(size_key(Metric.FILE_LINES), 826, 900, 826, id="grew"),
        pytest.param(size_key(Metric.FILE_LINES), 826, None, 826, id="gone"),
        pytest.param(size_key(Metric.FILE_LINES), 826, 790, None, id="below the threshold"),
        pytest.param(size_key(Metric.FILE_LINES), 826, 0, None, id="zero"),
        pytest.param(size_key(Metric.CCN, "work"), 19, 17, 17, id="ccn"),
        pytest.param(size_key(Metric.CCN, "work"), 17, 15, None, id="ccn below the threshold"),
        pytest.param(size_key(Metric.FUNCTION_LINES, "work"), 70, 62, 62, id="a function"),
    ],
)
def test_each_held_entry_falls_goes_or_stays(key, held, measured, expected):
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
    fix = fixed({key: held}, {key: held + offset})
    expected = {key: held + written_offset}
    assert (fix.ceiling.size, load_baseline(render_baseline(fix.ceiling)).size) == (expected, expected)


def test_a_new_key_never_enters():
    file, function = size_key(Metric.FILE_LINES), size_key(Metric.FUNCTION_LINES, "work")
    assert fixed({file: 826}, {file: 812, function: 90}).ceiling.size == {file: 812}


def test_a_pair_the_tree_lost_goes_and_a_new_one_stays_out():
    fix = fixed({}, {}, pairs=frozenset({pair("rest", "c")}), held_pairs=frozenset({pair("work", "b")}))
    assert fix.ceiling.clones == frozenset()
    assert Note("src/a.py", NoteEvent.DROPPED, "pair src/a.py work <-> src/b.py work is gone") in fix.changes


def test_a_file_without_clones_records_its_pairs():
    fix = lower_ceiling(Baseline({}, None), {}, frozenset({pair("work", "b")}))
    assert fix.ceiling.clones == frozenset({pair("work", "b")})
    assert [change.event for change in fix.changes] == [NoteEvent.RECORDED]


def test_a_lowered_entry_is_noted_with_its_fall_and_a_dropped_one_with_its_threshold():
    file, complexity = size_key(Metric.FILE_LINES), size_key(Metric.CCN, "work")
    fix = fixed({file: 826, complexity: 19}, {file: 790, complexity: 17}, held_pairs=frozenset())
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
