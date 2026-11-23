"""The `naming` rule, the names a repository writes itself (the distribution, import packages and commands of each
`pyproject.toml`, the `name` of each `package.json`, the repository's own name) against the stem its remote `origin`
names, and the deviations the operator ruled."""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from functools import cache
from pathlib import Path, PurePosixPath

from packaging.utils import canonicalize_name

from quality import layout, repository
from quality.fleet_sources import FLEET_PREFIX
from quality.report import CannotJudge, Finding, FindingEvent, Instruction, Unparsable
from quality.rules import Rule

DEVIATIONS_FILE = Path(__file__).with_name("naming_deviations.toml")
READ_FROM_ORIGIN = "read from remote origin"
ECOSYSTEM_NPM_PREFIX = "slidev-addon-"
ROLE_SUFFIXES = ("runtime", "service", "client", "server", "lib", "sdk", "api")
NOT_JUDGED_DIRECTORIES = frozenset({"node_modules", layout.TESTS_DIRECTORY, layout.FIXTURES_DIRECTORY})
REPOSITORY_SOURCE = ".git/config"
NO_ORIGIN_FACT = "no GitHub remote called origin names this repository"
NO_ORIGIN_STEP = Instruction("add the repository's GitHub remote as origin")


class NameKind(StrEnum):
    """What a name is the name of; the words a finding and a deviation entry use."""

    DISTRIBUTION = "distribution"
    IMPORT = "import"
    CLI = "CLI"
    NPM = "npm"
    REPOSITORY = "repository"


@dataclass(frozen=True)
class Deviation:
    """A name the operator ruled to stay as it is in one repository, with the item that holds the ruling."""

    repository: str
    kind: NameKind
    name: str
    ruling: str


@dataclass(frozen=True)
class _Claim:
    """A name a tracked file writes, and whether it is the distribution of a project nothing installs."""

    path: str
    kind: NameKind
    found: str
    uninstalled: bool = False


@dataclass(frozen=True)
class _Form:
    """A form a name may take: how it reads in a finding, and the regular expression the name must fully match."""

    shown: str
    pattern: str


@cache
def packaged_deviations() -> tuple[Deviation, ...]:
    """The deviations quality ships, read once; a new one takes effect with a quality release."""
    document = repository.parse_toml(DEVIATIONS_FILE.read_text(encoding="utf-8"))
    return tuple(
        Deviation(entry["repository"], NameKind(entry["kind"]), entry["name"], entry["ruling"]) for entry in document["deviation"]
    )


def check_naming(
    modes: Mapping[str, str],
    pyprojects: Mapping[str, bytes],
    package_jsons: Mapping[str, bytes],
    origin: str | None,
    deviations: Sequence[Deviation],
) -> list[Finding]:
    """Each name a `pyproject.toml` or `package.json` writes, and the repository's name, is a form of the stem unless a
    deviation covers it. `modes` maps each tracked path to its index mode; `pyprojects` and `package_jsons` hold the
    content of the manifests the tree holds that `is_judged`, by tracked path.

    `origin` is the `owner/name` of the remote `origin`, whose name is the stem; a checkout with no such remote cannot
    be judged. The findings carry the names the files write, the stem and fixed texts.
    """
    if origin is None:
        raise CannotJudge(NO_ORIGIN_FACT, (NO_ORIGIN_STEP,))
    claims = _claims(modes, pyprojects, package_jsons)
    stem = origin.partition("/")[2]
    findings = [_misnamed(claim, stem) for claim in claims if not _accepted(claim, stem) and not _deviates(claim, origin, deviations)]
    problem = _repository_problem(stem)
    if problem and not _deviates(_Claim(REPOSITORY_SOURCE, NameKind.REPOSITORY, stem), origin, deviations):
        findings.append(_repository_finding(stem, *problem))
    return sorted(findings, key=lambda finding: (finding.path, finding.fact))


def _claims(modes: Mapping[str, str], pyprojects: Mapping[str, bytes], package_jsons: Mapping[str, bytes]) -> list[_Claim]:
    packages = {_file(project.directory, layout.PROJECT_FILE): project.packages for project in layout.python_projects(modes)}
    return [
        *(claim for path, content in pyprojects.items() for claim in _python_claims(path, content, packages[path])),
        *(claim for path, content in package_jsons.items() for claim in _npm_claims(path, content)),
    ]


def is_judged(path: str) -> bool:
    """Whether the manifest at `path` is no dependency's and no test fixture's: none of its folders is `node_modules`,
    `tests` or `fixtures`."""
    return NOT_JUDGED_DIRECTORIES.isdisjoint(PurePosixPath(path).parent.parts)


def _file(directory: str, name: str) -> str:
    return str(PurePosixPath(directory) / name)


def _python_claims(path: str, content: bytes, packages: Sequence[str]) -> list[_Claim]:
    """The distribution, import packages and commands a `pyproject.toml` with a `[project]` table writes."""
    try:
        document = repository.parse_toml_file(path, content)
    except repository.TomlError as error:
        raise Unparsable(str(error)) from error
    project = document.get("project")
    if not isinstance(project, dict):
        return []
    uninstalled = _table(document, "tool", "uv").get("package") is False
    scripts = _table(project, "scripts")
    named = project.get("name")
    if "name" in project and not isinstance(named, str):
        raise Unparsable(f"{path} has a project name that is no string")
    return [
        *([_Claim(path, NameKind.DISTRIBUTION, named, uninstalled)] if "name" in project else []),
        *(_Claim(path, NameKind.IMPORT, package) for package in packages),
        *(_Claim(path, NameKind.CLI, script) for script in scripts),
    ]


def _table(parent: Mapping[str, object], *keys: str) -> Mapping[str, object]:
    """The table at `keys` below `parent`, empty where a key is missing or holds no table."""
    for key in keys:
        parent = value if isinstance(value := parent.get(key), dict) else {}
    return parent


def _npm_claims(path: str, content: bytes) -> list[_Claim]:
    document = layout.package_document(path, content)
    if "name" not in document:
        return []
    named = document["name"]
    if not isinstance(named, str):
        raise Unparsable(f"{path} has a name that is no string")
    return [_Claim(path, NameKind.NPM, named)]


def _with_component(base: str, separator: str) -> _Form:
    return _Form(f"{base}[{separator}<component>]", f"{re.escape(base)}(?:{re.escape(separator)}[a-z0-9]+)*")


def _forms(claim: _Claim, stem: str) -> list[_Form]:
    """The forms the stem allows for the kind of `claim`."""
    match claim.kind:
        case NameKind.DISTRIBUTION:
            canonical = canonicalize_name(stem)
            prefixed = _with_component(f"{FLEET_PREFIX}{canonical}", "-")
            return [prefixed, _with_component(canonical, "-")] if claim.uninstalled else [prefixed]
        case NameKind.IMPORT:
            return [_with_component(stem.replace("-", "_"), "_")]
        case NameKind.CLI:
            return [_with_component(stem, "-")]
        case _:
            ecosystem = f"{ECOSYSTEM_NPM_PREFIX}{stem}"
            return [_with_component(f"{FLEET_PREFIX}{stem}", "-"), _with_component(stem, "-"), _Form(ecosystem, re.escape(ecosystem))]


def _compared(kind: NameKind, name: str) -> str:
    """The name as its ecosystem compares it: a distribution after PEP 503, every other name as written."""
    return canonicalize_name(name) if kind is NameKind.DISTRIBUTION else name


def _accepted(claim: _Claim, stem: str) -> bool:
    name = _compared(claim.kind, claim.found)
    return any(re.fullmatch(form.pattern, name) for form in _forms(claim, stem))


def _deviates(claim: _Claim, origin: str, deviations: Sequence[Deviation]) -> bool:
    return any(
        deviation.repository.casefold() == origin.casefold()
        and deviation.kind is claim.kind
        and _compared(deviation.kind, deviation.name) == _compared(claim.kind, claim.found)
        for deviation in deviations
    )


def _misnamed(claim: _Claim, stem: str) -> Finding:
    forms = " or ".join(form.shown for form in _forms(claim, stem))
    fact = f'{claim.kind} "{claim.found}" does not fit the stem "{stem}", {READ_FROM_ORIGIN}'
    return Finding(Rule.NAMING, claim.path, None, fact, f"name it {forms}", event=FindingEvent.MISNAMED)


def _repository_problem(stem: str) -> tuple[str, str] | None:
    """The fact and the fix for a repository name that breaks the stem's own rules; None for one that keeps them."""
    if stem != stem.lower():
        return "has a capital", "rename the repository in lower case and point origin at it"
    suffix = next((suffix for suffix in ROLE_SUFFIXES if stem.endswith(f"-{suffix}")), None)
    if suffix is None:
        return None
    return f'ends in the role suffix "-{suffix}"', "rename the repository without the role suffix, point origin at it and keep the role in its description"


def _repository_finding(stem: str, fact: str, fix: str) -> Finding:
    return Finding(Rule.NAMING, REPOSITORY_SOURCE, None, f'{NameKind.REPOSITORY} name "{stem}" {fact}, {READ_FROM_ORIGIN}', fix, event=FindingEvent.MISNAMED)
