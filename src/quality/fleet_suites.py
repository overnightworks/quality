"""The fleet report's tests per layer: the columns, heading and cells of one table, beside the counts `suites` makes."""

from quality.suites import Layer, Suites

HEADING = "**Tests per layer**"
COLUMNS = ("Repo", *(layer.value for layer in Layer), "Skipped", "Fixme", "Note")
NONE = "—"


def cells(name: str, suites: Suites) -> tuple[str, ...]:
    noun = "file" if suites.unread == 1 else "files"
    note = f"{suites.unread} {noun} not read" if suites.unread else ""
    return name, *(f"{suites.layers[layer]:,}" for layer in Layer), f"{suites.skipped:,}", f"{suites.fixme:,}", note


def unjudged_cells(name: str, reason: str) -> tuple[str, ...]:
    return name, *(NONE,) * (len(COLUMNS) - 2), reason
