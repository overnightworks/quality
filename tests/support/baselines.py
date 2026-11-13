"""A baseline a test holds as `quality check --fix` would write it."""

from quality.baseline_file import (
    Baseline,
    ClonePair,
    JobMinutes,
    LiteralTexts,
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
    literal_texts: LiteralTexts | None = None,
) -> Baseline:
    """The baseline a test holds, every table named by keyword so a table the test leaves out is absent."""
    return Baseline(size, clones, job_minutes, literal_texts=literal_texts)
