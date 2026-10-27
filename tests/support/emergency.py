import json

from quality import cli
from quality.config import CONFIG_PATH
from quality.emergency import GhFailed
from quality.hygiene import CannotJudge
from quality.ratchet import BASELINE_PATH

REPO = "acme/widgets"


SHA = "a" * 40


BASE_SHA = "b" * 40


MERGE_SHA = "d" * 40


GATE_CONFIG = '[gate]\nmandatory_jobs = ["checks"]\nlegacy = "fail"\n'


LEGACY_RED = {"quality / sonar": "failure", "quality / verdict": "failure"}


ALL_METHODS_ALLOWED = {"allow_merge_commit": True, "allow_squash_merge": True, "allow_rebase_merge": True}


ONE_JOB = '[gate]\nmandatory_jobs = ["checks"]\n'


SONAR_GREEN = {"quality / sonar": "success", "quality / verdict": "failure"}


SHOWN = "declared loosening (overnightworks/songmaker#1345 of 2026-10-20): "


def declaring(what, reason="ruled"):
    """A [[gate.loosened]] entry; JSON's string escapes are TOML's."""
    return (
        f'[[gate.loosened]]\nwhat = {json.dumps(what)}\nitem = "overnightworks/songmaker#1345"\ndate = 2026-10-20\n'
        f"reason = {json.dumps(reason)}\n"
    )


class FakeGh:
    """Answers the gh calls the command makes and records every one; `fail_on` names a call to break."""

    def __init__(
        self, admin=True, checks=None, statuses=None, state="open", fail_on=(), interrupt_on=(), merge_lands=False,
        head_config=GATE_CONFIG, base_config=GATE_CONFIG, merge_config=None, merge_sha=MERGE_SHA,
        base_baseline=None, merge_baseline=None, base_sha=BASE_SHA, remote=None, extra_runs=(), settings=None, rules=((),), base_ref="main", rules_error=None, rules_raw=None,
    ):
        self.admin, self.state, self.fail_on, self.merge_lands = admin, state, set(fail_on), merge_lands
        self.interrupt_on = list(interrupt_on)
        self.settings = ALL_METHODS_ALLOWED if settings is None else settings
        self.rules, self.base_ref, self.rules_error, self.rules_raw = rules, base_ref, rules_error, rules_raw
        self.merge_sha, self.base_sha, self.remote = merge_sha, base_sha, remote
        self.files = {
            (CONFIG_PATH, SHA): head_config,
            (CONFIG_PATH, base_sha): base_config,
            (CONFIG_PATH, merge_sha): head_config if merge_config is None else merge_config,
            (BASELINE_PATH, base_sha): base_baseline,
            (BASELINE_PATH, merge_sha): merge_baseline,
        }
        self.checks = {"checks": "success", **(LEGACY_RED if checks is None else checks)}
        self.extra_runs = list(extra_runs)
        self.statuses = statuses or {}
        self.merged = False
        self.calls: list[tuple[str, ...]] = []
        self.sequence: list[str] = []
        self.gate_values: list[str] = []
        self.comments: list[str] = []

    def __call__(self, arguments, stdin=None):
        self.calls.append(tuple(arguments))
        step = self.step_of(arguments)
        if step == "merge" and self.merge_lands:
            self.merged = True
        if step in self.interrupt_on:
            raise KeyboardInterrupt
        if step in self.fail_on:
            raise GhFailed(f"{step} broke")
        return self.answer(step, arguments, stdin)

    @staticmethod
    def step_of(arguments):
        text = " ".join(arguments)
        for marker, step in (
            ("check-runs", "checks"), ("/status", "statuses"), ("contents/", "config"), ("/pulls/", "pull"),
            ("/rules/branches/", "rules"), ("properties/values", "gate"), ("pr merge", "merge"), ("/comments", "comment"), ("--jq .login", "login"),
        ):
            if marker in text:
                return step
        return "repository"

    @staticmethod
    def file_of(arguments):
        """The (path, sha) a contents call asks for; a file the fake does not hold answers 404."""
        path, _, sha = arguments[-1].partition("/contents/")[2].partition("?ref=")
        return path, sha

    def answer(self, step, arguments, stdin):
        if step == "pull":
            return json.dumps({"state": self.state, "merged": self.merged, "head": {"sha": SHA}, "base": {"sha": self.base_sha, "ref": self.base_ref}, "merge_commit_sha": self.merge_sha})
        if step == "checks":
            return "\n".join(
                json.dumps({"name": name, "status": "in_progress" if result is None else "completed", "conclusion": result})
                for name, result in [*self.checks.items(), *self.extra_runs]
            )
        if step == "statuses":
            return "\n".join(json.dumps({"context": context, "state": state}) for context, state in self.statuses.items())
        if step == "config":
            content = self.files.get(self.file_of(arguments))
            if content is None:
                raise GhFailed("gh api failed: HTTP 404")
            return content
        if step == "repository":
            return json.dumps({"default_branch": "main", "permissions": {"admin": self.admin}, **self.settings})
        if step == "rules":
            return self.rules_answer()
        if step == "login":
            return "felix\n"
        self.record(step, arguments, stdin)
        return ""

    def rules_answer(self):
        if self.rules_error:
            raise GhFailed(self.rules_error)
        if self.rules_raw is not None:
            return self.rules_raw
        return json.dumps(self.rules)

    def record(self, step, arguments, stdin):
        if step == "merge":
            self.merged = True
            self.sequence.append("merge")
        if step == "gate":
            self.gate_values.append(json.loads(stdin)["properties"][0]["value"])
            self.sequence.append(f"gate {self.gate_values[-1]}")
        if step == "comment":
            self.comments.append(next(a for a in arguments if a.startswith("body=")))
            self.sequence.append("comment")

    @property
    def merge_calls(self):
        return [call for call in self.calls if call[:2] == ("pr", "merge")]


def no_network(repository):
    raise AssertionError(f"the door reached for {repository}'s remote")


def sonar(new=0, legacy=4, main_analysed=True, analysed=SHA, unreachable=False):
    def transport(url, authorization):
        if unreachable:
            raise CannotJudge("SonarCloud is down")
        if "project_branches" in url:
            return {"branches": [{"name": "main", "isMain": True, **({"analysisDate": "x"} if main_analysed else {})}]}
        if "project_pull_requests" in url:
            return {"pullRequests": [{"key": "6", "commit": {"sha": "0" * 40}}, {"key": "7", "commit": {"sha": analysed}}]}
        if "measures" in url:
            return {}
        if "pullRequest=" in url:
            issues = [{"component": "k:a.py", "line": 3, "rule": "s1", "message": "bad"}][:new]
            return {"total": new, "issues": issues}
        return {"total": legacy, "issues": []}

    return transport


def drive(gh, transport=None, typed="ja", terminal=True, repo=REPO):
    answers = []

    def ask(prompt):
        answers.append(prompt)
        if isinstance(typed, BaseException):
            raise typed
        return typed

    remote = getattr(gh, "remote", None)
    code = cli.main(
        ["emergency-merge", repo, "7"], gh=gh, transport=transport or sonar(), ask=ask, is_terminal=lambda: terminal,
        remote_for=no_network if remote is None else lambda repository: remote,
    )
    return code, answers


def changed_nothing(gh):
    assert gh.gate_values == []
    assert gh.merge_calls == []
