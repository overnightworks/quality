"""What quality's own installation holds: the exact version its metadata requires of a dependency, and the executable
an installed distribution recorded."""

from collections.abc import Iterable
from importlib.metadata import PackageNotFoundError, distribution, requires
from os.path import normpath
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

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
