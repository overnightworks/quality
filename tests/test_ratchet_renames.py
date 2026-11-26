
import pytest

from quality.baseline_file import (
    BASELINE_PATH,
    ClonePair,
    JobKey,
    Key,
    Metric,
    Place,
    load_baseline,
    render_baseline,
)
from quality.ratchet import (
    RaisedEntry,
    Renames,
    carried_base,
    follow_renames,
    named_paths,
    raised_entries,
)
from tests.support.baselines import ceiling
from tests.support.door import FIX_WORDS, door_in
from tests.support.git_repositories import make_repo
from tests.support.source_code import (
    branchy_function,
    long_file,
    long_function,
    method,
    typescript_file,
)

QUOTED_PATH = 'src/straße "q".py'


@pytest.mark.parametrize(
    ("base", "head", "expected"),
    [
        (
            {Key(Metric.CCN, "a.py", "f"): 16, Key(Metric.FUNCTION_LINES, "a.py", "f"): 70},
            {Key(Metric.CCN, "a.py", "f"): 18, Key(Metric.FUNCTION_LINES, "a.py", "f"): 65},
            [RaisedEntry(Key(Metric.CCN, "a.py", "f"), 16, 18)],
        ),
        ({Key(Metric.FILE_LINES, "a.py"): 900}, {Key(Metric.FILE_LINES, "a.py"): 900}, []),
        ({Key(Metric.FILE_LINES, "a.py"): 900}, {}, []),
        ({}, {Key(Metric.FILE_LINES, "b.py"): 800}, [RaisedEntry(Key(Metric.FILE_LINES, "b.py"), None, 800)]),
        (
            {Key(Metric.CCN, "a.py", "f"): 16},
            {Key(Metric.CCN, "b.py", "f"): 16},
            [RaisedEntry(Key(Metric.CCN, "b.py", "f"), None, 16)],
        ),
    ],
    ids=["ccn raised while lines fell", "equal", "entry removed", "entry added", "entry moved"],
)
def test_only_a_raised_or_added_entry_loosens_the_baseline(base, head, expected):
    """BASELINE-2, BASELINE-3, BASELINE-7: a raised entry and an entry the base lacks (moved to another path included)
    loosen; a lowered, equal or removed entry does not."""
    assert raised_entries(base, head) == expected


def sized(path):
    return Key(Metric.CCN, path, "f")


def renamed(moves, unnamed_sources=()):
    return Renames(moves, frozenset(moves.values()) | frozenset(unnamed_sources))


def paired(first, second):
    return ClonePair.of(Place(first, "f"), Place(second, "g"))


@pytest.mark.parametrize(
    ("renames", "base", "head", "expected"),
    [
        ({"a.py": "b.py"}, {sized("a.py"): 16}, {sized("b.py"): 16}, {sized("b.py"): 16}),
        ({"a.py": "b.py"}, {sized("a.py"): 16, sized("b.py"): 99}, {sized("b.py"): 16}, {sized("b.py"): 16}),
        ({"a.py": "b.py"}, {sized("b.py"): 99}, {sized("b.py"): 16}, {}),
        ({"a.py": "b.py"}, {sized("a.py"): 16}, {sized("a.py"): 16, sized("b.py"): 16}, {sized("a.py"): 16}),
        ({"a.py": "b.py"}, {sized("a.py"): 16, sized("c.py"): 17}, {sized("b.py"): 16}, {sized("b.py"): 16, sized("c.py"): 17}),
        ({"a.py": "b.py", "b.py": "c.py"}, {sized("a.py"): 16, sized("b.py"): 17}, {sized("c.py"): 17}, {sized("b.py"): 16}),
    ],
    ids=["the allowance moves", "a leftover under the target is dropped", "a lone leftover is dropped",
         "nothing moves while the head holds the old path", "an unrelated path stays",
         "leftovers under every target are dropped before the sources move"],
)
def test_the_base_allowance_moves_to_a_rename_target_and_leftovers_there_do_not_count(renames, base, head, expected):
    """BASELINE-12, BASELINE-13, BASELINE-16: the old entry leaves its path unless the head still names it; an entry
    under the target never counts."""
    assert carried_base(ceiling(base, None), renamed(renames), named_paths(ceiling(head, None))).size == expected


@pytest.mark.parametrize(
    ("renames", "base", "head", "expected"),
    [
        ({"a.py": "b.py"}, {paired("a.py", "z.py")}, {paired("b.py", "z.py")}, {paired("b.py", "z.py")}),
        ({"a.py": "z.py"}, {paired("a.py", "b.py")}, {paired("z.py", "b.py")}, {ClonePair.of(Place("z.py", "f"), Place("b.py", "g"))}),
        ({"a.py": "c.py", "b.py": "d.py"}, {paired("a.py", "b.py")}, {paired("c.py", "d.py")}, {paired("c.py", "d.py")}),
        ({"a.py": "b.py"}, {paired("a.py", "z.py"), paired("b.py", "y.py")}, {paired("b.py", "z.py")}, {paired("b.py", "z.py")}),
        ({"a.py": "b.py"}, {paired("a.py", "z.py")}, {paired("a.py", "z.py"), paired("b.py", "z.py")}, {paired("a.py", "z.py")}),
    ],
    ids=["a side moves", "a moved side changes the canonical order", "both sides move", "a leftover pair under the target is dropped",
         "nothing moves while the head holds the old path"],
)
def test_a_clone_pair_moves_with_its_files_by_the_same_rules(renames, base, head, expected):
    """BASELINE-12, BASELINE-13, BASELINE-16, BASELINE-17: a pair whose sides only moved is the same pair, the sides
    re-sort through ClonePair.of, and a side the head still names does not move."""
    carried = carried_base(ceiling({}, frozenset(base)), renamed(renames), named_paths(ceiling({}, frozenset(head))))
    assert carried.clones == frozenset(expected)


def test_a_target_whose_source_cannot_be_named_is_cleared_and_nothing_moves_to_it():
    """BASELINE-14: a rename from a path that is not UTF-8 still drops the leftovers under its target."""
    base = ceiling({sized("new.py"): 200}, frozenset({paired("new.py", "z.py")}))
    carried = carried_base(base, renamed({}, unnamed_sources=["new.py"]), named_paths(ceiling({sized("new.py"): 150}, frozenset())))
    assert carried == ceiling({}, frozenset())


def test_a_head_pair_keeps_the_old_path_occupied_for_size_entries_too():
    """BASELINE-16: the head holds the old path through a size entry or a pair side, either blocks the move."""
    base = ceiling({sized("a.py"): 16}, frozenset({paired("a.py", "z.py")}))
    head = ceiling({sized("b.py"): 16}, frozenset({paired("a.py", "z.py"), paired("b.py", "z.py")}))
    assert carried_base(base, renamed({"a.py": "b.py"}), named_paths(head)) == ceiling({sized("a.py"): 16}, frozenset({paired("a.py", "z.py")}))


def test_a_base_without_a_clone_table_stays_without_one():
    """BASELINE-9: a base without a clone table is still without one after its renames are carried."""
    assert carried_base(ceiling({sized("a.py"): 16}, None), renamed({"a.py": "b.py"}), named_paths(ceiling({sized("b.py"): 16}, None))).clones is None


def booked(path, job="e2e"):
    return JobKey(path, job)


def job_baseline(entries, size=None):
    return ceiling(size or {}, None, entries)


@pytest.mark.parametrize(
    ("renames", "base", "head", "expected"),
    [
        ({"a.yml": "b.yml"}, {booked("a.yml"): 40}, {booked("b.yml"): 40}, {booked("b.yml"): 40}),
        ({"a.yml": "b.yml"}, {booked("a.yml"): 40, booked("b.yml"): 99}, {booked("b.yml"): 40}, {booked("b.yml"): 40}),
        ({"a.yml": "b.yml"}, {booked("b.yml"): 99}, {booked("b.yml"): 40}, {}),
        ({"a.yml": "b.yml"}, {booked("a.yml"): 40}, {booked("a.yml"): 40, booked("b.yml"): 40}, {booked("a.yml"): 40}),
        ({"a.yml": "b.yml"}, {booked("a.yml"): 40, booked("c.yml"): 20}, {booked("b.yml"): 40}, {booked("b.yml"): 40, booked("c.yml"): 20}),
    ],
    ids=["the allowance moves", "a leftover under the target is dropped", "a lone leftover is dropped",
         "nothing moves while the head holds the old path", "an unrelated path stays"],
)
def test_the_base_job_limits_move_to_a_rename_target_by_the_same_rules_as_size_entries(renames, base, head, expected):
    """TIMEOUT-17, BASELINE-12, BASELINE-13, BASELINE-16."""
    carried = carried_base(job_baseline(base), renamed(renames), named_paths(job_baseline(head)))
    assert carried.job_minutes == expected


def test_a_base_without_a_job_table_stays_without_one_and_a_held_empty_one_stays_held():
    """TIMEOUT-17, BASELINE-9."""
    rename = renamed({"a.yml": "b.yml"})
    absent, empty = (carried_base(job_baseline(entries), rename, frozenset()) for entries in (None, {}))
    assert (absent.job_minutes, empty.job_minutes) == (None, {})


def test_a_path_only_a_job_limit_names_is_a_path_the_baseline_names():
    """TIMEOUT-17, BASELINE-16: a head that names the old path only by a job entry keeps the move from happening."""
    assert "a.yml" in named_paths(job_baseline({booked("a.yml"): 40}))


@pytest.mark.parametrize("pairs", [None, frozenset()], ids=["no clone table", "an empty clone table"])
def test_a_carried_base_keeps_the_job_table_beside_the_size_entries_and_pairs(pairs):
    """TIMEOUT-17: the size entries move with a rename and the job table is handed on, whatever the clone table is."""
    base = ceiling({sized("a.py"): 16}, pairs, {booked("ci.yml"): 40})
    carried = carried_base(base, renamed({"a.py": "b.py"}), frozenset())
    assert (carried.size, carried.job_minutes) == ({sized("b.py"): 16}, {booked("ci.yml"): 40})


@pytest.mark.parametrize("pairs", [None, frozenset({paired("a.py", "z.py")})], ids=["no clone table", "a clone table"])
@pytest.mark.parametrize("jobs", [None, {}, {booked("ci.yml"): 40}], ids=["no job table", "an empty job table", "an entry"])
def test_following_renames_hands_a_held_job_table_on_unchanged(pairs, jobs):
    """TIMEOUT-17, FIX-31: the fix that moves size entries rewrites the job table byte for byte and never drops it."""
    committed = ceiling({sized("a.py"): 16}, pairs, jobs)
    held = ceiling({sized("b.py"): 16}, pairs and frozenset({paired("b.py", "z.py")}), jobs)
    fix = follow_renames(held, committed, renamed({"a.py": "b.py"}), frozenset({"b.py"}))
    assert fix.ceiling.job_minutes == jobs


def test_a_key_with_quotes_or_unicode_matches_itself_exactly():
    """BASELINE-2, BASELINE-7: a key with quotes or unicode is the same entry on both sides, so equal is no raise and a
    raise on it is found."""
    key = Key(Metric.CCN, QUOTED_PATH, 'Holder."méthode"')
    base = load_baseline(render_baseline(ceiling({key: 16}, None))).size
    assert raised_entries(base, load_baseline(render_baseline(ceiling({key: 16}, None))).size) == []
    assert raised_entries(base, load_baseline(render_baseline(ceiling({key: 17}, None))).size) == [RaisedEntry(key, 16, 17)]


@pytest.mark.integration
def test_a_tree_with_typescript_holds_the_same_python_baseline(tmp_path):
    """SIZE-4: a file of another language adds no Python entry and moves none."""
    python_only = {"src/b.py": long_file(810) + method(61), "src/a.py": branchy_function(17) + long_function(62)}
    mixed = {**python_only, "src/ui.ts": typescript_file(100), "src/Card.svelte": typescript_file(30)}
    written = []
    for name, files in (("python", python_only), ("mixed", mixed)):
        (tmp_path / name).mkdir()
        repo = make_repo(tmp_path / name, files)
        assert door_in(repo, *FIX_WORDS) == 0
        written.append((repo / BASELINE_PATH).read_text(encoding="utf-8"))
    assert written[0] == written[1]
