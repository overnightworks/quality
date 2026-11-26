"""The two tables whose added entry the verdict and the door judge as a loosening."""

import datetime
from collections.abc import Callable
from dataclasses import dataclass

import pytest

from tests.support.layout import declaring, root_declaring

RULING = ("#9", datetime.date(2026, 11, 24))
RULED = "#9 of 2026-11-24"
FIX = "fix: write the operator's ruling that lets it in on the entry in quality/config.toml, its item and its date"


@dataclass(frozen=True)
class Table:
    """One of the two tables: an entry as the config writes it, the line the verdict prints for an added one, and two
    names whose entries git may rename into each other."""

    entry: Callable[..., str]
    line: str
    first: str
    second: str

    def what(self, name: str, ends: str = "#2") -> str:
        return self.line.format(name=name, ends=ends)

    def written_before_the_keys(self) -> str:
        """The first name's entry as a base written before `ends` was: a multi-line reason and a key this release refuses."""
        return self.entry(self.first, reason="two\nlines", ends=None) + "unknown = 1\n"


TABLES = {
    "root": Table(root_declaring, "root allowance adds {name} until {ends}", "Makefile", "GNUmakefile"),
    "layout": Table(
        lambda name, **keys: declaring(name, rule="test-layout", **keys),
        "layout allowance adds {name} for test-layout until {ends}", "tests/a.py", "tests/b.py",
    ),
}
EACH_TABLE = pytest.mark.parametrize("table", TABLES.values(), ids=TABLES.keys())
