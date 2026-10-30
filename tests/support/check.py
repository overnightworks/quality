import re
import unicodedata
from collections.abc import Callable
from importlib.metadata import requires
from pathlib import Path

import pytest

from quality import DISTRIBUTION, check, cli
from quality.rules import Rule

installed_ruff = cli.INSTALLED_RUFF

CLEAN_VERDICT = "✓ clean · "


def is_clean(output: str) -> bool:
    """The run ended in the clean verdict line, which the door prints only on exit 0 with no finding."""
    return any(line.startswith(CLEAN_VERDICT) for line in output.splitlines())


def refused(output: str, area: str) -> bool:
    """The run named `area` as one it could not check."""
    return any(line.startswith(f"  ✗ {area} ") for line in output.splitlines())


def loosen_requirement(site: Path, monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    """Puts temporary metadata for quality first on the search path, its real requirements with `name`'s `==` turned into `>=`."""
    lines = [line.replace("==", ">=") if line.startswith(f"{name}==") else line for line in requires(DISTRIBUTION) or ()]
    dist_info = site / f"{DISTRIBUTION.replace('-', '_')}-0.dist-info"
    dist_info.mkdir(parents=True)
    requirements = "".join(f"Requires-Dist: {line}\n" for line in lines)
    (dist_info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {DISTRIBUTION}\nVersion: 0\n{requirements}")
    monkeypatch.syspath_prepend(str(site))


def assert_every_line_starts_with_the_door(lines: list[str]) -> None:
    """#97 rule 2, cell 18 and #228 rule 7: no line repository text could forge, and no raw control, format or line-separator
    character (categories Cc, Cf, Zl, Zp, listed here by hand from the rule) between the lines."""
    for line in lines:
        assert not line.lstrip().startswith(("::", "File ", "raise ")), line
        assert all(unicodedata.category(character) not in ("Cc", "Cf", "Zl", "Zp") for character in line), line


def hygiene_output(
    capsys: pytest.CaptureFixture[str], path: Path, locate_ruff: Callable[[], str] = installed_ruff
) -> tuple[int, str]:
    """The exit code and the whole output of `quality hygiene` over `path`, through the real door."""
    code = cli.main(["hygiene", str(path)], locate_ruff=locate_ruff, clock=lambda: 0.0)
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def compose(*port_lines: str) -> dict[str, str]:
    body = "".join(f"      {line}\n" for line in port_lines)
    return {"compose.yaml": f"services:\n  web:\n    image: x\n    ports:\n{body}"}


def assert_refusal(output: str, rule: str, path: str) -> None:
    assert re.search(rf"^{rule} {re.escape(path)} - \S.*\n  Fix: \S", output, re.MULTILINE), output


def refusal_row(area: str) -> str:
    """What an area row that could not check says before its reason (docs/output.md, case 4)."""
    return f"  ✗ {area:<12}  {'not checked':<14} "


def hygiene_run(repo: Path, locate_ruff: Callable[[], str] = installed_ruff, locate_zizmor: Callable[[], str] | None = None) -> check.HygieneResult:
    """What the rule runner judged over `repo`, before the door prints it."""
    return check.run_hygiene(repo, locate_ruff, locate_zizmor or cli.INSTALLED_ZIZMOR)


def refusals(repo: Path, locate_ruff: Callable[[], str] = installed_ruff, locate_zizmor: Callable[[], str] | None = None) -> dict[Rule, str]:
    """Each rule that cannot judge `repo`, with its reason, as the rule runner reports it."""
    verdicts = hygiene_run(repo, locate_ruff, locate_zizmor).verdicts
    return {verdict.rule: str(verdict.refusal) for verdict in verdicts if verdict.refusal is not None}


def heard(capsys: pytest.CaptureFixture[str]) -> str:
    """Everything the door printed since the last read, stdout then stderr."""
    captured = capsys.readouterr()
    return captured.out + captured.err
