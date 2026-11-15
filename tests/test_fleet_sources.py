import pytest

from quality.report import FindingEvent
from quality.rules import Rule
from tests.support.check import check_result, refusals, rule_findings
from tests.support.git_repositories import git

INDEX = 'registry = "https://pypi.org/simple"'
WHEEL_URL = (
    "https://github.com/overnightworks/design/releases/download/v1.2.0/overnightworks_design-1.2.0-py3-none-any.whl"
)
SOURCES = {
    "git": 'git = "https://github.com/overnightworks/logline?rev=v0.2.1#0123456789abcdef"',
    "url": f'url = "{WHEEL_URL}"',
    "editable": 'editable = "."',
    "virtual": 'virtual = "."',
    "directory": 'directory = "packages/client"',
    "path": 'path = "../client"',
}


def lock(name: str, source: str | None, version: str = "0.2.1") -> str:
    """A uv.lock of the package `name` resolved from `source` (a TOML inline table body), or from none."""
    resolved = "" if source is None else f"source = {{ {source} }}\n"
    return f'version = 1\n\n[[package]]\nname = "{name}"\nversion = "{version}"\n{resolved}'


def findings_of(files: dict[str, str], build_repo):
    return rule_findings(build_repo(files), Rule.FLEET_SOURCE)


def test_a_fleet_package_resolved_from_an_index_is_a_finding_at_its_lock(build_repo):
    """SOURCE-1: a fleet package a tracked uv.lock resolves from an index is `from index`, once, at the lock."""
    (finding,) = findings_of({"uv.lock": lock("overnightworks-logline", INDEX)}, build_repo)
    assert (finding.path, finding.event) == ("uv.lock", FindingEvent.FROM_INDEX)
    assert all(word in finding.fact for word in ("overnightworks-logline", "0.2.1", "https://pypi.org/simple"))
    assert "git+https://github.com/overnightworks/" in str(finding.fix)


def test_a_nested_lock_names_its_tracked_path(build_repo):
    """SOURCE-1: the finding sits at the tracked path of a lock below the root."""
    (finding,) = findings_of({"service/uv.lock": lock("overnightworks-tandem", INDEX)}, build_repo)
    assert finding.path == "service/uv.lock"


@pytest.mark.parametrize("kind", SOURCES)
def test_a_fleet_package_from_any_source_but_an_index_is_clean(build_repo, kind):
    """SOURCE-2: a git, url, editable, virtual, directory or path source is allowed."""
    assert findings_of({"uv.lock": lock("overnightworks-design", SOURCES[kind])}, build_repo) == []


@pytest.mark.parametrize("name", ["pyyaml", "my-overnightworks-tool", "overnightworks"])
def test_a_package_outside_the_fleet_prefix_from_an_index_is_clean(build_repo, name):
    """SOURCE-3: only a package named overnightworks-* is judged."""
    assert findings_of({"uv.lock": lock(name, INDEX)}, build_repo) == []


def test_the_prefix_is_read_in_its_canonical_spelling(build_repo):
    """SOURCE-3: `Overnightworks_Logline` is the package `overnightworks-logline`."""
    (finding,) = findings_of({"uv.lock": lock("Overnightworks_Logline", INDEX)}, build_repo)
    assert finding.event is FindingEvent.FROM_INDEX


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        ("version = [\n", "uv.lock is not UTF-8 TOML"),
        (lock("overnightworks-logline", None), "overnightworks-logline has no source"),
        ("version = 1\npackage = [1]\n", "uv.lock is not a uv lock"),
        ('version = 1\n[package]\nname = "overnightworks-x"\n', "uv.lock is not a uv lock"),
        (
            lock("overnightworks-logline", 'ftp = "x"'),
            "overnightworks-logline has a source of a kind quality does not know",
        ),
    ],
    ids=["unreadable lock", "no source", "packages not a list", "package a table", "unknown source kind"],
)
def test_a_lock_that_cannot_be_read_as_a_judgement_refuses_the_rule(build_repo, content, reason):
    """SOURCE-4: an unreadable lock, a fleet package without a source and an unknown source kind are exit 2, never clean."""
    assert reason in refusals(build_repo({"uv.lock": content}))[Rule.FLEET_SOURCE]


def test_a_tracked_lock_gone_from_disk_is_skipped(build_repo):
    """SOURCE-5: a lock the index tracks and the tree no longer holds is not judged."""
    repo = build_repo({"uv.lock": lock("overnightworks-logline", INDEX)})
    (repo / "uv.lock").unlink()
    assert rule_findings(repo, Rule.FLEET_SOURCE) == []
    assert Rule.FLEET_SOURCE not in refusals(repo)


def test_a_lock_linking_out_of_the_repository_refuses_the_rule(build_repo, tmp_path):
    """SOURCE-5: a lock that resolves outside the repository cannot be judged."""
    repo = build_repo({"uv.lock": "x\n"})
    outside = tmp_path.parent / f"{tmp_path.name}-outside.lock"
    outside.write_text(lock("overnightworks-logline", INDEX))
    (repo / "uv.lock").unlink()
    (repo / "uv.lock").symlink_to(outside)
    assert "uv.lock resolves outside the repository" in refusals(repo)[Rule.FLEET_SOURCE]


def test_a_lock_tracked_under_two_names_is_one_finding_named_by_its_first(build_repo):
    """SOURCE-6: `speech/uv.lock -> ../uv.lock` and `uv.lock` are one file, judged and named once."""
    repo = build_repo({"uv.lock": lock("overnightworks-logline", INDEX)})
    (repo / "speech").mkdir()
    (repo / "speech/uv.lock").symlink_to("../uv.lock")
    git(repo, "add", "speech/uv.lock")
    git(repo, "commit", "-q", "-m", "link")
    (finding,) = rule_findings(repo, Rule.FLEET_SOURCE)
    assert finding.path == "speech/uv.lock"


@pytest.mark.parametrize(
    ("files", "locks"),
    [({"README.md": "x\n"}, 0), ({"uv.lock": lock("pyyaml", INDEX), "a/uv.lock": lock("pyyaml", INDEX)}, 2)],
    ids=["no lock", "two locks"],
)
def test_the_extent_is_the_count_of_locks_read(build_repo, files, locks):
    """SOURCE-7: a repository without a uv.lock is clean at extent 0; each lock read counts."""
    (verdict,) = (v for v in check_result(build_repo(files)).verdicts if v.rule is Rule.FLEET_SOURCE)
    assert (verdict.findings, verdict.extent) == ((), locks)


def test_the_rule_judges_when_the_advisory_rule_cannot(build_repo):
    """SOURCE-8: a refused config refuses the advisory rule, and the fleet sources are still judged."""
    repo = build_repo(
        {"uv.lock": lock("overnightworks-logline", INDEX), "quality/config.toml": "[secrets]\nignore = []\n"}
    )
    result = check_result(repo)
    assert Rule.ADVISORY in refusals(repo)
    assert [finding.event for finding in result.findings if finding.rule == Rule.FLEET_SOURCE] == [
        FindingEvent.FROM_INDEX
    ]


def test_the_advisory_rule_judges_when_the_rule_cannot(build_repo):
    """SOURCE-8: a fleet package without a source refuses only this rule; the advisory rule still judges the lock."""
    reasons = refusals(build_repo({"uv.lock": lock("overnightworks-logline", None)}))
    assert Rule.FLEET_SOURCE in reasons
    assert Rule.ADVISORY not in reasons
