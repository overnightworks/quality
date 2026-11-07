"""What quality's own installation holds: the exact version its metadata requires of a dependency, the executable an
installed distribution recorded, and where its installer got it."""

import json
from collections.abc import Iterable
from dataclasses import dataclass
from importlib.metadata import Distribution, PackageNotFoundError, distribution, requires
from os.path import normpath
from pathlib import Path
from urllib.parse import unquote, urlsplit

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

import quality
from quality import DISTRIBUTION


class UnpinnedDependency(Exception):
    """Quality's metadata does not pin a dependency to exactly one version."""

    def __init__(self, name: str) -> None:
        super().__init__(f"quality's own metadata does not pin {name} with one exact ==; this is a defect in quality's release")


def exact_version(requirements: Iterable[str], distribution_name: str) -> str:
    wanted = canonicalize_name(distribution_name)
    for line in requirements:
        requirement = Requirement(line)
        if canonicalize_name(requirement.name) != wanted:
            continue
        specifiers = list(requirement.specifier)
        if len(specifiers) == 1 and specifiers[0].operator == "==" and "*" not in specifiers[0].version:
            return specifiers[0].version
    raise UnpinnedDependency(distribution_name)


def required_version(distribution_name: str) -> str:
    return exact_version(requires(DISTRIBUTION) or (), distribution_name)


def installed_executable(distribution_name: str) -> str:
    """The file named like the distribution in its RECORD; never a search of PATH."""
    try:
        installed = distribution(distribution_name)
    except PackageNotFoundError as error:
        raise FileNotFoundError(f"{distribution_name} is not installed") from error
    recorded = next((entry for entry in installed.files or () if entry.name == distribution_name), None)
    if recorded is None:
        raise FileNotFoundError(f"the installation of {distribution_name} records no {distribution_name} executable")
    executable = normpath(Path(installed.locate_file(recorded)).absolute())
    if not Path(executable).is_file():
        raise FileNotFoundError(f"{executable} is not a file")
    return executable


@dataclass(frozen=True)
class VcsOrigin:
    """The installer fetched this installation from git at `commit`."""

    commit: str


@dataclass(frozen=True)
class DirectoryOrigin:
    """The installer built this installation from a local directory, editable or not."""

    directory: Path


def origin(installed: Distribution) -> VcsOrigin | DirectoryOrigin | None:
    """Where the installer got `installed`, as its `direct_url.json` records it (PEP 610); None for no record, a record
    of another kind, or one that does not parse: such an installation never judges at a pin."""
    try:
        record = json.loads(installed.read_text("direct_url.json") or "")
    except json.JSONDecodeError:
        return None
    if not isinstance(record, dict):
        return None
    vcs_info, url = record.get("vcs_info"), record.get("url")
    if isinstance(vcs_info, dict) and vcs_info.get("vcs") == "git" and isinstance(vcs_info.get("commit_id"), str):
        return VcsOrigin(vcs_info["commit_id"])
    if isinstance(record.get("dir_info"), dict) and isinstance(url, str) and urlsplit(url).scheme == "file":
        return DirectoryOrigin(Path(unquote(urlsplit(url).path)))
    return None


def package_directory() -> Path:
    """The directory this installation's code was imported from."""
    return Path(quality.__file__).parent
