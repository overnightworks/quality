"""The naming rule: the names a repository writes itself, judged against the stem its remote `origin` names."""

import json
import re
import subprocess

import pytest

from quality import naming
from quality.report import FindingEvent
from quality.rules import Rule
from tests.support.check import check_output, check_result, refusals, rows_of, rule_findings
from tests.support.git_repositories import GIT_IDENTITY, head_commit
from tests.support.github_remote import with_github_remote

SHOP = "overnightworks/shop"
GUIDANCE = {"HEART.md": "# guidance\n"}


def pyproject(name: str | None = "overnightworks-shop", *, scripts: tuple[str, ...] = (), uninstalled: bool = False) -> str:
    lines = ["[project]"]
    lines += [] if name is None else [f'name = "{name}"']
    lines += ['version = "1"', 'requires-python = ">=3.14"']
    if scripts:
        lines += ["[project.scripts]", *(f'{script} = "x:y"' for script in scripts)]
    if uninstalled:
        lines += ["[tool.uv]", "package = false"]
    return "\n".join(lines) + "\n"


def package_json(name: str) -> str:
    return json.dumps({"name": name, "version": "1.0.0"})


def packages(*names: str, project: str = "src") -> dict[str, str]:
    return {f"{project}/{name}/__init__.py": "" for name in names}


@pytest.fixture
def judge(build_repo):
    """The naming findings, as (path, found name) pairs, over the files in a repository whose origin is `repository`."""

    def judging(files: dict[str, str], repository: str = SHOP) -> list[tuple[str, str]]:
        repo = with_github_remote(build_repo({**GUIDANCE, **files}), repository)
        findings = rule_findings(repo, Rule.NAMING)
        return [(finding.path, re.search(r'"([^"]+)"', finding.fact)[1]) for finding in findings]

    return judging


@pytest.mark.parametrize(
    ("name", "accepted"),
    [
        pytest.param("overnightworks-shop", True, id="the prefixed stem"),
        pytest.param("overnightworks-shop-worker", True, id="a component"),
        pytest.param("overnightworks-shop-fetch-big-model", True, id="a component of several words"),
        pytest.param("Overnightworks_Shop.Worker", True, id="PEP 503 normalisation"),
        pytest.param("shop", False, id="the bare stem"),
        pytest.param("overnightworks-other", False, id="another stem"),
        pytest.param("overnightworks-shopper", False, id="a longer word"),
        pytest.param("overnightworks-shop-", False, id="an empty component"),
    ],
)
def test_a_distribution_is_the_prefixed_stem_with_an_optional_component_after_pep_503(judge, name, accepted):
    """N2, N3."""
    assert (judge({"pyproject.toml": pyproject(name)}) == []) is accepted


@pytest.mark.parametrize(
    ("name", "accepted"),
    [
        pytest.param("shop", True, id="the bare stem"),
        pytest.param("shop-legal", True, id="a bare component"),
        pytest.param("overnightworks-shop", True, id="the prefixed stem"),
        pytest.param("legal", False, id="a name without the stem"),
    ],
)
def test_a_distribution_nothing_installs_may_keep_the_bare_stem(judge, name, accepted):
    """N3: `package = false`."""
    assert (judge({"pyproject.toml": pyproject(name, uninstalled=True)}) == []) is accepted


def test_a_member_of_a_workspace_is_a_component(judge):
    """N3: every tracked pyproject.toml alike, root or not."""
    files = {
        "pyproject.toml": pyproject("overnightworks-shop"),
        "worker/pyproject.toml": pyproject("overnightworks-shop-worker"),
        **packages("shop"),
        **packages("shop_worker", project="worker/src"),
    }
    assert judge(files) == []


def test_a_member_outside_the_stem_is_red_at_its_own_file(judge):
    files = {"pyproject.toml": pyproject(), "legal/pyproject.toml": pyproject("legal", uninstalled=True), **packages("legal", project="legal/src")}
    assert judge(files) == [("legal/pyproject.toml", "legal"), ("legal/pyproject.toml", "legal")]


@pytest.mark.parametrize(
    ("package", "accepted"),
    [
        pytest.param("shop", True, id="the stem"),
        pytest.param("shop_worker", True, id="a component"),
        pytest.param("shop_fetch_big_model", True, id="a component of several words"),
        pytest.param("gitmirror", False, id="another name"),
        pytest.param("shopper", False, id="a longer word"),
        pytest.param("shop_", False, id="an empty component"),
    ],
)
def test_an_import_package_is_the_stem_with_an_optional_underscore_component(judge, package, accepted):
    """N2, N3: imports compare exactly."""
    assert (judge({"pyproject.toml": pyproject(), **packages(package)}) == []) is accepted


@pytest.mark.parametrize(
    ("repository", "package", "accepted"),
    [
        pytest.param("overnightworks/gmail-cleanup", "gmail_cleanup", True, id="underscores for the stem's hyphen"),
        pytest.param("overnightworks/gmail-cleanup", "gmail_cleanup_rules", True, id="a component"),
        pytest.param("overnightworks/gmail-cleanup", "gmail", False, id="half of the stem"),
    ],
)
def test_an_import_writes_each_hyphen_of_the_stem_as_an_underscore(judge, repository, package, accepted):
    files = {"pyproject.toml": pyproject("overnightworks-gmail-cleanup"), **packages(package)}
    assert (judge(files, repository) == []) is accepted


@pytest.mark.parametrize(
    ("script", "accepted"),
    [
        pytest.param("shop", True, id="the stem"),
        pytest.param("shop-run", True, id="a component"),
        pytest.param("shop-run-fast", True, id="a component of several words"),
        pytest.param("shop_run", False, id="an underscore"),
        pytest.param("other", False, id="another name"),
        pytest.param("shopper", False, id="a longer word"),
    ],
)
def test_a_command_is_the_stem_with_an_optional_hyphenated_component(judge, script, accepted):
    """N3: CLI names compare exactly."""
    assert (judge({"pyproject.toml": pyproject(scripts=(script,))}) == []) is accepted


@pytest.mark.parametrize(
    ("name", "accepted"),
    [
        pytest.param("overnightworks-shop", True, id="the prefixed stem"),
        pytest.param("overnightworks-shop-web", True, id="a prefixed component"),
        pytest.param("shop", True, id="the bare stem"),
        pytest.param("shop-web", True, id="a bare component"),
        pytest.param("slidev-addon-shop", True, id="a Slidev addon"),
        pytest.param("other", False, id="another name"),
        pytest.param("@overnightworks/shop", False, id="a scoped name"),
        pytest.param("Overnightworks-Shop", False, id="npm names compare exactly"),
    ],
)
def test_an_npm_name_is_a_stem_form_or_an_ecosystem_form(judge, name, accepted):
    """N4."""
    assert (judge({"web/package.json": package_json(name)}) == []) is accepted


@pytest.mark.parametrize(
    "path",
    [
        "web/node_modules/dep/package.json", "fixtures/sample/package.json", "tests/fixtures/sample/package.json", "tests/sample/package.json",
        "web/tests/sample/package.json",
    ],
)
def test_a_package_json_in_node_modules_tests_or_a_fixture_is_not_judged(judge, path):
    """N4."""
    files = {"pyproject.toml": pyproject(), path: package_json("foreign")}
    assert judge(files) == []


@pytest.mark.parametrize("path", ["tests/sample/pyproject.toml", "tests/fixtures/sample/pyproject.toml", "fixtures/sample/pyproject.toml"])
def test_a_pyproject_in_tests_or_a_fixture_is_not_judged_even_beside_no_root_project(judge, path):
    """N3: a test fixture carries a name of its own."""
    assert judge({path: pyproject("foreign"), **packages("foreign", project=path.removesuffix("/pyproject.toml") + "/src")}) == []


def test_a_gitlink_at_a_manifest_path_is_never_entered(build_repo, elsewhere):
    """A submodule's own files carry its own names."""
    inner = build_repo({"pyproject.toml": pyproject("foreign")})
    repo = elsewhere / "outer"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    with_github_remote(repo, SHOP)
    subprocess.run(["git", "-C", str(repo), "update-index", "--add", "--cacheinfo", "160000", head_commit(inner), "vendor/pyproject.toml"], check=True)
    subprocess.run(["git", "-C", str(repo), *GIT_IDENTITY, "commit", "-q", "-m", "outer"], check=True)
    (repo / "vendor" / "pyproject.toml").mkdir(parents=True)
    assert rule_findings(repo, Rule.NAMING) == []


def test_a_manifest_without_a_name_has_nothing_to_judge(judge):
    files = {"pyproject.toml": pyproject(None), "web/package.json": json.dumps({"private": True})}
    assert judge(files) == []


def test_each_wrong_kind_is_a_finding_of_its_own_at_its_file(judge):
    files = {
        "pyproject.toml": pyproject("wrong-dist", scripts=("wrong-cli",)),
        **packages("wrong_import"),
        "web/package.json": package_json("wrong-npm"),
    }
    assert sorted(judge(files)) == [
        ("pyproject.toml", "wrong-cli"), ("pyproject.toml", "wrong-dist"), ("pyproject.toml", "wrong_import"), ("web/package.json", "wrong-npm"),
    ]


def test_a_finding_names_the_kind_the_name_found_the_accepted_forms_and_the_stem_it_read(build_repo):
    repo = with_github_remote(build_repo({**GUIDANCE, "pyproject.toml": pyproject("wrong")}), SHOP)
    (finding,) = (found for found in rule_findings(repo, Rule.NAMING) if "distribution" in found.fact)
    assert (finding.event, finding.path, finding.line) == (FindingEvent.MISNAMED, "pyproject.toml", None)
    assert 'distribution "wrong"' in finding.fact
    assert 'stem "shop"' in finding.fact
    assert "overnightworks-shop[-<component>]" in str(finding.fix)


def test_the_stem_is_the_name_of_origin_and_no_other_remote(build_repo):
    repo = with_github_remote(build_repo({**GUIDANCE, "pyproject.toml": pyproject("overnightworks-shop")}), "overnightworks/other", remote="upstream")
    with_github_remote(repo, SHOP)
    assert rule_findings(repo, Rule.NAMING) == []


def test_a_red_name_prints_as_a_misnamed_row_and_makes_the_run_red(build_repo, capsys):
    repo = with_github_remote(build_repo({**GUIDANCE, "pyproject.toml": pyproject("wrong")}), SHOP)
    code, printed = check_output(capsys, repo)
    (finding,) = rule_findings(repo, Rule.NAMING)
    assert (code, len(rows_of(printed, finding))) == (1, 1)


@pytest.mark.parametrize("suffix", naming.ROLE_SUFFIXES)
def test_a_repository_name_with_a_role_suffix_is_red_with_no_file_to_judge(judge, suffix):
    """N6, marketplace#571 line 3."""
    assert judge({}, f"overnightworks/shop-{suffix}") == [(".git/config", f"shop-{suffix}")]


def test_a_repository_name_with_a_capital_is_red(judge):
    """N2."""
    assert judge({}, "overnightworks/Shop") == [(".git/config", "Shop")]


@pytest.mark.parametrize("name", ["shop", "gmail-cleanup", "runtime-shop", "apiary"])
def test_a_repository_name_without_a_role_suffix_is_clean(judge, name):
    assert judge({}, f"overnightworks/{name}") == []


def test_no_github_origin_refuses_a_repository_that_holds_a_name_to_judge(build_repo):
    """N1: as AGENTS-13."""
    reasons = refusals(build_repo({**GUIDANCE, "pyproject.toml": pyproject()}))
    assert "origin" in reasons[Rule.NAMING]


def test_no_github_origin_leaves_a_repository_with_no_name_clean(build_repo):
    repo = build_repo(GUIDANCE)
    assert (Rule.NAMING in refusals(repo), rule_findings(repo, Rule.NAMING)) == (False, [])


def test_a_remote_that_is_not_called_origin_does_not_stand_for_it(build_repo):
    repo = with_github_remote(build_repo({**GUIDANCE, "pyproject.toml": pyproject()}), SHOP, remote="upstream")
    assert "origin" in refusals(repo)[Rule.NAMING]


def test_a_pyproject_that_is_not_toml_cannot_be_judged(build_repo):
    repo = with_github_remote(build_repo({**GUIDANCE, "pyproject.toml": "[project\n"}), SHOP)
    assert "pyproject.toml" in refusals(repo)[Rule.NAMING]


def test_a_package_json_that_is_not_json_cannot_be_judged(build_repo):
    repo = with_github_remote(build_repo({**GUIDANCE, "web/package.json": "{"}), SHOP)
    assert "web/package.json" in refusals(repo)[Rule.NAMING]


FIRST_DEVIATION = naming.packaged_deviations()[0]


def test_a_packaged_deviation_covers_its_name_in_its_repository_only(build_repo):
    files = {**GUIDANCE, "pyproject.toml": pyproject(FIRST_DEVIATION.name)}
    covered = with_github_remote(build_repo(files), FIRST_DEVIATION.repository)
    assert rule_findings(covered, Rule.NAMING) == []


def test_a_packaged_deviation_covers_no_other_repository(judge):
    assert judge({"pyproject.toml": pyproject(FIRST_DEVIATION.name)}) == [("pyproject.toml", FIRST_DEVIATION.name)]


def test_a_packaged_deviation_covers_no_other_name_of_its_repository(judge):
    files = {"pyproject.toml": pyproject(f"{FIRST_DEVIATION.name}-other")}
    assert judge(files, FIRST_DEVIATION.repository) == [("pyproject.toml", f"{FIRST_DEVIATION.name}-other")]


@pytest.mark.parametrize("deviation", naming.packaged_deviations(), ids=lambda deviation: f"{deviation.repository} {deviation.kind} {deviation.name}")
def test_every_packaged_deviation_names_a_repository_a_kind_a_name_and_a_ruling(deviation):
    assert re.fullmatch(r"[^/]+/[^/]+", deviation.repository)
    assert deviation.name
    assert deviation.ruling


def test_the_rule_is_in_the_files_area_and_on_for_every_repository():
    assert (Rule.NAMING.area.value, Rule.NAMING.release.switch.value) == ("files", "always")


def test_the_run_names_the_rule_in_its_files_area(build_repo):
    repo = with_github_remote(build_repo(GUIDANCE), SHOP)
    assert Rule.NAMING in [verdict.rule for verdict in check_result(repo).verdicts]
