"""A spec file a test holds, and the declarations of `quality/config.toml` that judge its IDs."""

import datetime

from quality.config_entries import CONFIG_PATH
from tests.support.config_refusals import entry_text

SPEC = "docs/rules.md"


def spec_with(*lines: str) -> dict[str, str]:
    """The files of a repository whose one spec file holds `lines`."""
    return {SPEC: "\n".join(lines) + "\n"}


def foreign_entry(prefix: str) -> str:
    """The `[[spec_citations.foreign]]` entry that declares `prefix` an implemented contract's."""
    return entry_text("spec_citations.foreign", {"prefix": prefix, "owner": "overnightworks/design"})


def implemented_entry(
    path: str,
    item: str = "#974",
    date: datetime.date = datetime.date(2026, 11, 23),
    reason: str = "its implementers' tests cite its rules",
) -> str:
    """The `[[spec_citations.implemented]]` entry that declares the document at `path` a contract others implement."""
    return entry_text("spec_citations.implemented", {"path": path, "item": item, "date": date, "reason": reason})


def declaring(*entries: str) -> dict[str, str]:
    """The files of a repository whose `quality/config.toml` holds `entries`."""
    return {CONFIG_PATH: "".join(entries)}
