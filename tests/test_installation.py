import importlib.metadata
import os
from pathlib import Path

import pytest

from quality import installation
from quality.installation import UnpinnedDependency


@pytest.mark.parametrize("name", ["ruff", "zizmor"])
def test_the_version_quality_requires_is_the_version_its_environment_installed(name):
    """#248 rule 4: one source for the pin, and `uv sync --locked` installs exactly it."""
    assert installation.required_version(name) == importlib.metadata.version(name)


@pytest.mark.parametrize(
    ("asked", "lines", "version"),
    [
        ("x", ["x==1.2.3"], "1.2.3"),
        ("x-y", ["X_y==1.2.3"], "1.2.3"),
        ("x", ["other>=1", "x==2.0"], "2.0"),
    ],
    ids=["exact", "canonicalised name", "among other requirements"],
)
def test_an_exact_requirement_gives_its_version(asked, lines, version):
    assert installation.exact_version(lines, asked) == version


@pytest.mark.parametrize(
    "lines",
    [["x>=1"], ["x==1,<2"], ["x==1.2.*"], ["x===1.2.3"], ["y==1"], []],
    ids=["lower bound", "two specifiers", "wildcard", "arbitrary equality", "another name", "no requirement"],
)
def test_a_requirement_that_is_not_one_exact_version_is_a_packaging_defect(lines):
    with pytest.raises(UnpinnedDependency, match="x"):
        installation.exact_version(lines, "x")


def test_a_dependency_quality_does_not_require_is_a_packaging_defect():
    with pytest.raises(UnpinnedDependency, match="not-required"):
        installation.required_version("not-required")


@pytest.mark.parametrize("name", ["ruff", "zizmor"])
def test_the_executable_is_the_one_the_installation_recorded_never_one_on_path(tmp_path, monkeypatch, name):
    planted = tmp_path / name
    planted.write_text("#!/bin/sh\n")
    planted.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    executable = installation.installed_executable(name)
    assert (Path(executable).name, Path(executable).is_file()) == (name, True)
    assert executable != str(planted)
    assert os.path.normpath(executable) == executable


def install_distribution(site: Path, record: str, name: str = "scanner") -> None:
    dist_info = site / f"{name}-1.0.dist-info"
    dist_info.mkdir(parents=True)
    (dist_info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: 1.0\n")
    if record:
        (dist_info / "RECORD").write_text(record)


@pytest.mark.parametrize(
    "record",
    [None, "", "scanner-1.0.dist-info/METADATA,,\n", "scanner-1.0.dist-info/METADATA,,\n../bin/scanner,,\n"],
    ids=["not installed", "no RECORD", "RECORD without the executable", "recorded file gone"],
)
def test_an_executable_the_installation_does_not_hold_is_not_found(tmp_path, monkeypatch, record):
    site = tmp_path / "site"
    if record is not None:
        install_distribution(site, record)
    site.mkdir(exist_ok=True)
    monkeypatch.syspath_prepend(str(site))
    with pytest.raises(FileNotFoundError):
        installation.installed_executable("scanner")
