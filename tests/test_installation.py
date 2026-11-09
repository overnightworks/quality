import importlib.metadata
import os
import re
from pathlib import Path

import pytest

from quality import installation
from quality.installation import UnpinnedDependency


@pytest.mark.parametrize("name", ["ruff", "zizmor"])
def test_the_version_quality_requires_is_the_version_its_environment_installed(name):
    """SCANNER-14: the version quality's metadata requires of ruff and zizmor is the version installed with it."""
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
    """SCANNER-14: an exact `==` requirement gives its version, among others and under any spelling of the name."""
    assert installation.exact_version(lines, asked) == version


@pytest.mark.parametrize(
    "lines",
    [["scanner>=1"], ["scanner==1,<2"], ["scanner==1.2.*"], ["scanner===1.2.3"], ["other==1"], []],
    ids=["lower bound", "two specifiers", "wildcard", "arbitrary equality", "another name", "no requirement"],
)
def test_a_requirement_that_is_not_one_exact_version_is_a_packaging_defect(lines):
    """SCANNER-18: a requirement that is not one exact version is a packaging defect."""
    with pytest.raises(UnpinnedDependency, match=re.escape("pin scanner ")):
        installation.exact_version(lines, "scanner")


def test_a_dependency_quality_does_not_require_is_a_packaging_defect():
    """SCANNER-18: a dependency quality does not require is a packaging defect."""
    with pytest.raises(UnpinnedDependency, match="not-required"):
        installation.required_version("not-required")


@pytest.mark.parametrize("name", ["ruff", "zizmor"])
def test_the_executable_is_the_one_the_installation_recorded_never_one_on_path(tmp_path, monkeypatch, name):
    """SCANNER-15: the executable is the one the installation recorded, never one on PATH."""
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
    """SCANNER-16: an executable the installation does not hold, or does not record, is not found."""
    site = tmp_path / "site"
    if record is not None:
        install_distribution(site, record)
    site.mkdir(exist_ok=True)
    monkeypatch.syspath_prepend(str(site))
    with pytest.raises(FileNotFoundError):
        installation.installed_executable("scanner")


def recorded(tmp_path: Path, record: str | None) -> importlib.metadata.Distribution:
    """A distribution whose metadata directory holds `record` as its `direct_url.json`, or no such file."""
    metadata = tmp_path / "overnightworks_quality-0.10.0.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text("Metadata-Version: 2.1\nName: overnightworks-quality\nVersion: 0.10.0\n")
    if record is not None:
        (metadata / "direct_url.json").write_text(record)
    return importlib.metadata.PathDistribution(metadata)


@pytest.mark.parametrize(
    ("record", "expected"),
    [
        pytest.param(
            '{"url": "https://github.com/acme/tool", "vcs_info": {"vcs": "git", "commit_id": "07bdf7c478ac9cc9cd348b180abd3cbc9d873f23"}}',
            installation.VcsOrigin("07bdf7c478ac9cc9cd348b180abd3cbc9d873f23"), id="a git install",
        ),
        pytest.param(
            '{"url": "file:///home/someone/tool", "dir_info": {"editable": true}}', installation.DirectoryOrigin(Path("/home/someone/tool")),
            id="an editable directory",
        ),
        pytest.param('{"url": "file:///srv/my%20tool", "dir_info": {}}', installation.DirectoryOrigin(Path("/srv/my tool")), id="a directory"),
        pytest.param(None, None, id="no record"),
        pytest.param('{"url": "https://example.invalid/tool.tar.gz", "archive_info": {}}', None, id="an archive URL"),
        pytest.param('{"url": "https://hg.example.invalid/tool", "vcs_info": {"vcs": "hg", "commit_id": "abc"}}', None, id="another vcs"),
        pytest.param("{not json", None, id="not JSON"),
    ],
)
def test_the_origin_is_what_the_installer_recorded(tmp_path, record, expected):
    """PIN-3: a git installation's commit is the one its installer recorded."""
    assert installation.origin(recorded(tmp_path, record)) == expected
