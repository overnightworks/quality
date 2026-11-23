"""A baseline a test holds as `quality check --fix` would write it."""

from quality.baseline_file import (
    Baseline,
    ClonePair,
    JobMinutes,
    Place,
    SizeEntries,
    load_baseline,
    render_baseline,
)


def rendered(tables: str) -> str:
    """`tables` as the rendering of the ceiling they hold: header, order and quoting as the fix writes them."""
    return render_baseline(load_baseline(tables))


def ceiling(
    size: SizeEntries,
    clones: frozenset[ClonePair] | None = None,
    job_minutes: JobMinutes | None = None,
    spec_citations: frozenset[str] | None = None,
) -> Baseline:
    """The baseline a test holds, every table named by keyword so a table the test leaves out is absent."""
    return Baseline(size, clones, job_minutes, spec_citations)


def spec_table(*identifiers: str) -> str:
    """The `[spec_citations]` table, as TOML text, that holds `identifiers` as uncited."""
    listed = "".join(f'"{identifier}", ' for identifier in identifiers)
    return f"[spec_citations]\nuncited = [{listed}]\n"


def holding_spec_ids(*identifiers: str) -> str:
    """The rendered baseline of a repository whose `[spec_citations]` table holds `identifiers` as uncited."""
    return rendered(f"[file_lines]\n\n[clones]\n\n{spec_table(*identifiers)}")


# Every state of the baseline a report-only debt rule must leave as it is; None is no baseline file.
DEBT_BASELINE_STATES = {
    "a baseline held": render_baseline(ceiling({}, frozenset())),
    "no baseline": None,
    "a baseline without clones": render_baseline(ceiling({})),
    "a red area": render_baseline(ceiling({}, frozenset({ClonePair.of(Place("tests/a.py", "x"), Place("tests/b.py", "y"))}))),
    "a baseline that cannot be read": "[size\n",
}
