"""A baseline a test holds as `quality check --fix` would write it."""

from quality.ratchet import load_baseline, render_baseline


def rendered(tables: str) -> str:
    """`tables` as the rendering of the ceiling they hold: header, order and quoting as the fix writes them."""
    return render_baseline(load_baseline(tables))
