"""The verdict's line for a `quality/config.toml` it cannot read, at the head and at the base commit."""

import pytest

from quality import cli
from quality.config import CONFIG_PATH
from tests.support.verdict import CHECKS_GREEN


def sonar_string_config():
    return '[gate]\nmandatory_jobs = ["checks"]\nsonar = "false"\n'


def refused_at_the_head(repo, commit_base):
    (repo / CONFIG_PATH).write_text(sonar_string_config())
    return "the head"


def refused_at_the_base_commit(repo, commit_base):
    sha = commit_base(None, sonar_string_config())
    (repo / CONFIG_PATH).write_text('[gate]\nmandatory_jobs = ["checks"]\n')
    return f"the base commit {sha}"


@pytest.mark.integration
@pytest.mark.parametrize("refuse", [refused_at_the_head, refused_at_the_base_commit], ids=["at the head", "at the base commit"])
def test_a_config_the_verdict_cannot_read_is_its_one_line(capsys, run_env, pull_request, refuse):
    (repo, summary), (_, commit_base) = run_env, pull_request
    where = refuse(repo, commit_base)
    code = cli.main(["verdict", str(repo)], fetcher_for=lambda token: lambda url: (CHECKS_GREEN, None))
    printed = capsys.readouterr()
    line = (
        f"verdict: cannot judge - quality/config.toml at {where}: quality/config.toml [gate] sonar must be true or false, "
        "like:\\x0a[gate]\\x0asonar = false"
    )
    assert (code, printed.out, printed.err, summary.read_text()) == (2, "", line + "\n", line + "\n")
