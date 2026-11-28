"""The ways a file the reader opens is refused, as the parameters of one test."""

import os
from collections.abc import Callable
from pathlib import Path

import pytest
from _pytest.mark.structures import ParameterSet

type Refusal = Callable[[Path, Path], None]


def unreadable(path: str, text: str, *, dangling_link: bool = False) -> list[ParameterSet]:
    """One parameter per way the tracked file `path`, which held `text`, is refused: a mode of 0, a link out of the
    repository, text that is not UTF-8 and, when `dangling_link`, a link to nothing. A parameter is `refuse(repo, elsewhere)`."""

    def by_mode(repo: Path, _elsewhere: Path) -> None:
        (repo / path).chmod(0)

    def by_link(repo: Path, elsewhere: Path) -> None:
        (elsewhere / "outside").write_text(text)
        (repo / path).unlink()
        (repo / path).symlink_to(elsewhere / "outside")

    def by_dangling_link(repo: Path, _elsewhere: Path) -> None:
        (repo / path).unlink()
        (repo / path).symlink_to(repo / "missing")

    def by_encoding(repo: Path, _elsewhere: Path) -> None:
        (repo / path).write_bytes(text.encode() + b"# \xff\xfe\n")

    skip_as_root = pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-0 file")
    refusals = [
        pytest.param(by_mode, id="mode 0", marks=skip_as_root),
        pytest.param(by_link, id="link out of the repository"),
        pytest.param(by_encoding, id="text that is not UTF-8"),
    ]
    if dangling_link:
        refusals.insert(2, pytest.param(by_dangling_link, id="dangling link"))
    return refusals
