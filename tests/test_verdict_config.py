"""The verdict's pair for a `quality/config.toml` it cannot read, at the head and at the base commit."""

import pytest

from quality import cli
from quality.config import CONFIG_PATH
from tests.support.verdict import CHECKS_GREEN, without_release_row

READABLE = '[gate]\nmandatory_jobs = ["checks"]\n'
SONAR_STRING = (
    READABLE + 'sonar = "false"\n',
    (
        "error: quality/config.toml [gate] sonar must be true or false\n"
        "next:\n"
        "    write it in quality/config.toml like this:\n"
        "        [gate]\n"
        "        sonar = false\n"
        "    {step}\n"
    ),
)
UNKNOWN_SECRETS_KEY = (
    READABLE + "[secrets]\nignore = []\n",
    "error: quality/config.toml has unknown key ignore in [secrets] - only allow exists\nnext: {step}\n",
)
HEAD_STEP = "push the fix; the gate judges the new commit"
BASE_STEP = "fix it on main first, then update the pull request's branch with main"


def refused_at_the_head(repo, commit_base, config):
    (repo / CONFIG_PATH).write_text(config)
    return HEAD_STEP


def refused_at_the_base_commit(repo, commit_base, config):
    commit_base(None, config)
    (repo / CONFIG_PATH).write_text(READABLE)
    return BASE_STEP


@pytest.mark.integration
@pytest.mark.parametrize(
    ("refuse", "config"),
    [
        pytest.param(refused_at_the_head, SONAR_STRING, id="at the head, with an example"),
        pytest.param(refused_at_the_head, UNKNOWN_SECRETS_KEY, id="at the head, without an example"),
        pytest.param(refused_at_the_base_commit, SONAR_STRING, id="at the base, with an example"),
        pytest.param(refused_at_the_base_commit, UNKNOWN_SECRETS_KEY, id="at the base, without an example"),
    ],
)
def test_a_config_the_verdict_cannot_read_is_its_fact_with_the_example_and_the_sides_step(capsys, run_env, pull_request, refuse, config):
    """O10, VERDICT-35, VERDICT-39, VERDICT-40, VERDICT-41."""
    (repo, summary), (_, commit_base) = run_env, pull_request
    text, pair = config
    pair = pair.format(step=refuse(repo, commit_base, text))
    code = cli.main(["verdict"], fetcher_for=lambda token: lambda url: (CHECKS_GREEN, None))
    printed = capsys.readouterr()
    assert (code, without_release_row(printed.out), printed.err, without_release_row(summary.read_text())) == (2, "", pair, f"```\n{pair}```\n")


@pytest.mark.integration
def test_a_config_refused_at_both_sides_is_fixed_on_the_base_first(capsys, run_env, pull_request):
    """VERDICT-42."""
    (repo, _), (_, commit_base) = run_env, pull_request
    refused_at_the_base_commit(repo, commit_base, SONAR_STRING[0])
    refused_at_the_head(repo, commit_base, UNKNOWN_SECRETS_KEY[0])
    code = cli.main(["verdict"], fetcher_for=lambda token: lambda url: (CHECKS_GREEN, None))
    assert (code, capsys.readouterr().err) == (2, SONAR_STRING[1].format(step=BASE_STEP))
