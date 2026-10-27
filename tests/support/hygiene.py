import unicodedata
from pathlib import Path

import pytest

from quality import cli


def assert_every_line_starts_with_the_door(lines: list[str]) -> None:
    """#97 rule 2, cell 18: no line repository text could forge, and no raw control character between the lines."""
    for line in lines:
        assert not line.lstrip().startswith(("::", "File ", "raise ")), line
        assert all(unicodedata.category(character) != "Cc" for character in line), line


def hygiene_output(capsys: pytest.CaptureFixture[str], path: Path) -> tuple[int, str]:
    """The exit code and the whole output of `quality hygiene` over `path`, through the real door."""
    code = cli.main(["hygiene", str(path)])
    captured = capsys.readouterr()
    return code, captured.out + captured.err
