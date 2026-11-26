"""The naming rule: the names a repository writes itself, judged against the stem its remote `origin` names."""

import json
import os
import re
import subprocess

import pytest

from quality import naming
from quality.report import FindingEvent
from quality.rules import Rule
from tests.support.check import check_output, refusals, rows_of, rule_findings
from tests.support.git_repositories import GIT_IDENTITY, git, head_commit, put
from tests.support.github_remote import with_github_remote

SHOP = "overnightworks/shop"
GUIDANCE = {"HEART.md": "# guidance\n"}


@pytest.fixture
def fixture_origin() -> None:
    """Each test gives its repository the remote it judges, or none."""
    return


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


@pytest.mark.integration
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
    """NAMING-6."""
    assert (judge({"pyproject.toml": pyproject(name)}) == []) is accepted


@pytest.mark.integration
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
    """NAMING-6."""
    assert (judge({"pyproject.toml": pyproject(name, uninstalled=True)}) == []) is accepted


@pytest.mark.integration
def test_a_member_of_a_workspace_is_a_component(judge):
    files = {
        "pyproject.toml": pyproject("overnightworks-shop"),
        "worker/pyproject.toml": pyproject("overnightworks-shop-worker"),
        **packages("shop"),
        **packages("shop_worker", project="worker/src"),
    }
    assert judge(files) == []


@pytest.mark.integration
def test_a_member_outside_the_stem_is_red_at_its_own_file(judge):
    files = {"pyproject.toml": pyproject(), "legal/pyproject.toml": pyproject("legal", uninstalled=True), **packages("legal", project="legal/src")}
    assert judge(files) == [("legal/pyproject.toml", "legal"), ("legal/pyproject.toml", "legal")]


@pytest.mark.integration
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
    """NAMING-7."""
    assert (judge({"pyproject.toml": pyproject(), **packages(package)}) == []) is accepted


@pytest.mark.integration
@pytest.mark.parametrize(
    ("repository", "package", "accepted"),
    [
        pytest.param("overnightworks/gmail-cleanup", "gmail_cleanup", True, id="underscores for the stem's hyphen"),
        pytest.param("overnightworks/gmail-cleanup", "gmail_cleanup_rules", True, id="a component"),
        pytest.param("overnightworks/gmail-cleanup", "gmail", False, id="half of the stem"),
    ],
)
def test_an_import_writes_each_hyphen_of_the_stem_as_an_underscore(judge, repository, package, accepted):
    """NAMING-7."""
    files = {"pyproject.toml": pyproject("overnightworks-gmail-cleanup"), **packages(package)}
    assert (judge(files, repository) == []) is accepted


@pytest.mark.integration
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
    """NAMING-8."""
    assert (judge({"pyproject.toml": pyproject(scripts=(script,))}) == []) is accepted


@pytest.mark.integration
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
    """NAMING-11."""
    assert (judge({"web/package.json": package_json(name)}) == []) is accepted


@pytest.mark.integration
@pytest.mark.parametrize(
    "path",
    [
        "web/node_modules/dep/package.json", "fixtures/sample/package.json", "tests/fixtures/sample/package.json", "tests/sample/package.json",
        "web/tests/sample/package.json",
    ],
)
def test_a_package_json_in_node_modules_tests_or_a_fixture_is_not_judged(judge, path):
    """NAMING-11."""
    files = {"pyproject.toml": pyproject(), path: package_json("foreign")}
    assert judge(files) == []


@pytest.mark.integration
@pytest.mark.parametrize("path", ["tests/sample/pyproject.toml", "tests/fixtures/sample/pyproject.toml", "fixtures/sample/pyproject.toml"])
def test_a_pyproject_in_tests_or_a_fixture_is_not_judged_even_beside_no_root_project(judge, path):
    """NAMING-5."""
    assert judge({path: pyproject("foreign"), **packages("foreign", project=path.removesuffix("/pyproject.toml") + "/src")}) == []


@pytest.mark.integration
@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-0 file, so no permission is refused")
@pytest.mark.parametrize("path", ["fixtures/sample/package.json", "tests/sample/pyproject.toml", "web/node_modules/dep/package.json"])
def test_a_manifest_that_is_not_judged_is_never_read_and_the_repository_name_is_still_judged(build_repo, path):
    repo = with_github_remote(build_repo({**GUIDANCE, path: "{}"}), "overnightworks/shop-api")
    (repo / path).chmod(0)
    assert (len(rule_findings(repo, Rule.NAMING)), Rule.NAMING in refusals(repo)) == (1, False)


@pytest.mark.integration
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


@pytest.mark.integration
@pytest.mark.parametrize(
    "files",
    [
        pytest.param({"pyproject.toml": pyproject(None)}, id="a project without a name"),
        pytest.param({"pyproject.toml": '[tool.uv.workspace]\nmembers = ["worker"]\n', **packages("legal")}, id="a pyproject without a project table"),
        pytest.param({"web/package.json": json.dumps({"private": True})}, id="a package.json without a name"),
        pytest.param({"web/package.json": '["name"]'}, id="a package.json that is no object"),
    ],
)
def test_a_manifest_without_a_name_has_nothing_to_judge(build_repo, files):
    """NAMING-12."""
    repo = with_github_remote(build_repo({**GUIDANCE, **files}), SHOP)
    assert (rule_findings(repo, Rule.NAMING), Rule.NAMING in refusals(repo)) == ([], False)


@pytest.mark.integration
def test_each_wrong_kind_is_a_finding_of_its_own_at_its_file(judge):
    files = {
        "pyproject.toml": pyproject("wrong-dist", scripts=("wrong-cli",)),
        **packages("wrong_import"),
        "web/package.json": package_json("wrong-npm"),
    }
    assert sorted(judge(files)) == [
        ("pyproject.toml", "wrong-cli"), ("pyproject.toml", "wrong-dist"), ("pyproject.toml", "wrong_import"), ("web/package.json", "wrong-npm"),
    ]


@pytest.mark.integration
def test_a_finding_names_the_kind_the_name_found_the_accepted_forms_and_the_stem_it_read(build_repo):
    """NAMING-14."""
    repo = with_github_remote(build_repo({**GUIDANCE, "pyproject.toml": pyproject("wrong")}), SHOP)
    (finding,) = (found for found in rule_findings(repo, Rule.NAMING) if "distribution" in found.fact)
    assert (finding.event, finding.path, finding.line) == (FindingEvent.MISNAMED, "pyproject.toml", None)
    assert 'distribution "wrong"' in finding.fact
    assert 'stem "shop"' in finding.fact
    assert "overnightworks-shop[-<component>]" in str(finding.fix)


@pytest.mark.integration
def test_a_repository_finding_names_where_the_stem_was_read_from_too(build_repo):
    """NAMING-3."""
    repo = with_github_remote(build_repo(GUIDANCE), "overnightworks/shop-api")
    (finding,) = rule_findings(repo, Rule.NAMING)
    assert "read from remote origin" in finding.fact


@pytest.mark.integration
def test_the_stem_is_the_name_of_origin_and_no_other_remote(build_repo):
    """NAMING-2."""
    repo = with_github_remote(build_repo({**GUIDANCE, "pyproject.toml": pyproject("overnightworks-shop")}), "overnightworks/other", remote="upstream")
    with_github_remote(repo, SHOP)
    assert rule_findings(repo, Rule.NAMING) == []


@pytest.mark.integration
def test_a_red_name_prints_as_a_misnamed_row_and_makes_the_run_red(build_repo, capsys):
    repo = with_github_remote(build_repo({**GUIDANCE, "pyproject.toml": pyproject("wrong")}), SHOP)
    code, printed = check_output(capsys, repo)
    (finding,) = rule_findings(repo, Rule.NAMING)
    assert (code, len(rows_of(printed, finding))) == (1, 1)


@pytest.mark.integration
@pytest.mark.parametrize("suffix", naming.ROLE_SUFFIXES)
def test_a_repository_name_with_a_role_suffix_is_red_with_no_file_to_judge(judge, suffix):
    """NAMING-13."""
    assert judge({}, f"overnightworks/shop-{suffix}") == [(".git/config", f"shop-{suffix}")]


@pytest.mark.integration
def test_a_repository_name_with_a_capital_is_red(judge):
    """NAMING-13."""
    assert judge({}, "overnightworks/Shop") == [(".git/config", "Shop")]


@pytest.mark.integration
@pytest.mark.parametrize("name", ["shop", "gmail-cleanup", "runtime-shop", "apiary"])
def test_a_repository_name_without_a_role_suffix_is_clean(judge, name):
    assert judge({}, f"overnightworks/{name}") == []


@pytest.mark.integration
def test_no_github_origin_refuses_a_repository_that_holds_a_name_to_judge(build_repo):
    """NAMING-4."""
    reasons = refusals(build_repo({**GUIDANCE, "pyproject.toml": pyproject()}))
    assert "origin" in reasons[Rule.NAMING]


@pytest.mark.integration
def test_no_github_origin_refuses_even_a_repository_with_no_name_to_judge(build_repo):
    """NAMING-4."""
    assert "origin" in refusals(build_repo(GUIDANCE))[Rule.NAMING]


@pytest.mark.integration
def test_a_remote_that_is_not_called_origin_does_not_stand_for_it(build_repo):
    repo = with_github_remote(build_repo({**GUIDANCE, "pyproject.toml": pyproject()}), SHOP, remote="upstream")
    assert "origin" in refusals(repo)[Rule.NAMING]


@pytest.mark.integration
@pytest.mark.parametrize(
    ("path", "content"),
    [
        pytest.param("pyproject.toml", "[project\n", id="a pyproject that is not TOML"),
        pytest.param("pyproject.toml", '[project]\nname = 1\n', id="a project name that is no string"),
        pytest.param("web/package.json", "{", id="a package.json that is not JSON"),
        pytest.param("web/package.json", '{"name": 1}', id="an npm name that is no string"),
        pytest.param("web/package.json", '{"name": null}', id="an npm name that is null"),
        pytest.param("web/package.json", '{"name": "shop"}'.encode("utf-16"), id="a package.json in UTF-16"),
        pytest.param("web/package.json", b'\xff{"name": "shop"}', id="a package.json that is no UTF-8"),
    ],
)
def test_a_manifest_that_is_not_read_as_utf_8_with_a_string_name_cannot_be_judged(build_repo, path, content):
    """NAMING-19."""
    repo = with_github_remote(build_repo(GUIDANCE), SHOP)
    put(repo / path, content)
    git(repo, "add", "-f", path)
    assert path in refusals(repo)[Rule.NAMING]


FIRST_DEVIATION = naming.packaged_deviations()[0]


@pytest.mark.integration
def test_a_packaged_deviation_covers_its_name_in_its_repository_only(build_repo):
    """NAMING-16."""
    files = {**GUIDANCE, "pyproject.toml": pyproject(FIRST_DEVIATION.name)}
    covered = with_github_remote(build_repo(files), FIRST_DEVIATION.repository)
    assert rule_findings(covered, Rule.NAMING) == []


@pytest.mark.integration
def test_a_packaged_deviation_covers_no_other_repository(judge):
    """NAMING-16."""
    assert judge({"pyproject.toml": pyproject(FIRST_DEVIATION.name)}) == [("pyproject.toml", FIRST_DEVIATION.name)]


@pytest.mark.integration
def test_a_packaged_deviation_covers_no_other_name_of_its_repository(judge):
    """NAMING-16."""
    files = {"pyproject.toml": pyproject(f"{FIRST_DEVIATION.name}-other")}
    assert judge(files, FIRST_DEVIATION.repository) == [("pyproject.toml", f"{FIRST_DEVIATION.name}-other")]


@pytest.mark.parametrize("deviation", naming.packaged_deviations(), ids=lambda deviation: f"{deviation.repository} {deviation.kind} {deviation.name}")
def test_every_packaged_deviation_names_a_repository_a_kind_a_name_and_a_ruling(deviation):
    assert re.fullmatch(r"[^/]+/[^/]+", deviation.repository)
    assert deviation.name
    assert deviation.ruling
