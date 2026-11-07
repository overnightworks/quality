import json
from datetime import UTC, datetime

from quality import cli
from quality.config import CONFIG_PATH
from quality.merge import GhFailed
from quality.ratchet import BASELINE_PATH
from quality.report import CannotJudge

REPO = "acme/widgets"


SHA = "a" * 40


ABSENT = object()


BASE_SHA = "b" * 40


MERGE_SHA = "d" * 40


GATE_CONFIG = '[gate]\nmandatory_jobs = ["checks"]\nlegacy = "fail"\n'


LEGACY_RED = {"quality / sonar": "failure", "quality / verdict": "failure"}


ALL_METHODS_ALLOWED = {"allow_merge_commit": True, "allow_squash_merge": True, "allow_rebase_merge": True}


ONE_JOB = '[gate]\nmandatory_jobs = ["checks"]\n'


SONAR_GREEN = {"quality / sonar": "success", "quality / verdict": "failure"}


TWO_JOBS = '[gate]\nmandatory_jobs = ["checks", "B"]\n'
B_REMOVED = 'mandatory job "B" removed'


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
        body="Work-Item: #3\n\nCloses #3", later_body=None, draft=False, raw_answers=None,
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
        self.body, self.later_body, self.draft, self.pulls_read = body, later_body, draft, 0
        self.merged = False
        self.raw_answers = raw_answers or {}
        self.requests: list[tuple[tuple[str, ...], str | None]] = []
        self.sequence: list[str] = []
        self.gate_values: list[str] = []
        self.comments: list[str] = []
        self.merge_messages: list[str | None] = []

    def __call__(self, arguments, stdin=None):
        self.requests.append((tuple(arguments), stdin))
        step = self.step_of(arguments)
        moment = self.moment_of(arguments, stdin)
        if step == "merge" and self.merge_lands:
            self.merged = True
        if moment in self.interrupt_on:
            raise KeyboardInterrupt
        if moment in self.fail_on:
            raise GhFailed(f"{moment} broke")
        return self.answer(step, arguments, stdin, moment)

    def moment_of(self, arguments, stdin):
        """The step a call belongs to, a gate request switching it on `restore` and a pull read after a merge `read back`."""
        step = self.step_of(arguments)
        if step == "gate" and json.loads(stdin)["properties"][0]["value"] == "true":
            return "restore"
        if step == "pull" and self.merge_calls:
            return "read back"
        return step

    @staticmethod
    def step_of(arguments):
        text = " ".join(arguments)
        for marker, step in (
            ("check-runs", "checks"), ("/status", "statuses"), ("contents/", "config"), ("/pulls/", "pull"),
            ("/rules/branches/", "rules"), ("properties/values", "gate"), ("pr merge", "merge"), ("/comments", "comment"),
        ):
            if marker in text:
                return step
        return "login" if list(arguments) == ["api", "user", "--jq", ".login"] else "repository"

    @staticmethod
    def file_of(arguments):
        """The (path, sha) a contents call asks for; a file the fake does not hold answers 404."""
        path, _, sha = arguments[-1].partition("/contents/")[2].partition("?ref=")
        return path, sha

    def answer(self, step, arguments, stdin, moment):
        fixed = self.fixed_answer(step, moment, arguments)
        if fixed is not None:
            return fixed
        if step == "pull":
            return self.pull_answer()
        if step == "checks":
            return "\n".join(
                json.dumps({"name": name, "status": "in_progress" if result is None else "completed", "conclusion": result})
                for name, result in [*self.checks.items(), *self.extra_runs]
            )
        if step == "statuses":
            return "\n".join(json.dumps({"context": context, "state": state}) for context, state in self.statuses.items())
        if step == "config":
            content = self.files.get(self.file_of(arguments))
            if isinstance(content, GhFailed):
                raise content
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

    def fixed_answer(self, step, moment, arguments):
        """A raw answer the test planted, or nothing for a check or status read of any commit but the head's own."""
        if moment in self.raw_answers:
            return self.raw_answers[moment]
        if step in ("checks", "statuses") and f"/commits/{SHA}/" not in " ".join(arguments):
            return ""
        return None

    def pull_answer(self):
        body = self.later_body if self.pulls_read and self.later_body is not None else self.body
        self.pulls_read += 1
        pull = {"state": self.state, "merged": self.merged, "head": {"sha": SHA}, "base": {"sha": self.base_sha, "ref": self.base_ref}, "merge_commit_sha": self.merge_sha}
        for name, value in (("body", body), ("draft", self.draft)):
            if value is not ABSENT:
                pull[name] = value
        return json.dumps(pull)

    def rules_answer(self):
        if self.rules_error:
            raise GhFailed(self.rules_error)
        if self.rules_raw is not None:
            return self.rules_raw
        return json.dumps(self.rules)

    def record(self, step, arguments, stdin):
        if step == "merge":
            self.merged = True
            self.merge_messages.append(stdin)
            self.sequence.append("merge")
        if step == "gate":
            self.gate_values.append(json.loads(stdin)["properties"][0]["value"])
            self.sequence.append(f"gate {self.gate_values[-1]}")
        if step == "comment":
            self.comments.append(next(a for a in arguments if a.startswith("body=")))
            self.sequence.append("comment")

    @property
    def calls(self):
        return [argv for argv, _ in self.requests]

    @property
    def merge_calls(self):
        return [call for call in self.calls if call[:2] == ("pr", "merge")]


def no_network(repository):
    raise AssertionError(f"the door reached for {repository}'s remote")


def sonar(new=0, legacy=4, main_analysed=True, analysed=SHA, unreachable=False, added=None):
    """`added` lists the pull request's open issues (`new` makes that many alike)."""
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
            issues = added if added is not None else [{"component": "k:a.py", "line": 3, "rule": "s1", "message": "bad"}][:new]
            return {"total": len(issues), "issues": issues}
        return {"total": legacy, "issues": []}

    return transport


def pull_row():
    return "  · #7            open           head aaaaaaa · into main\n"


def landing_row(item="#3"):
    return f"  · landing       work item      {item}\n"


def rows_before_the_prompt():
    """What the default `FakeGh` shows the operator, the landing row last."""
    return (
        pull_row()
        + "  ✓ checks        ok             only quality / sonar and quality / verdict are red\n"
        + "  ✓ loosenings    none\n"
        + "  ! sonar         4 legacy findings on main\n"
        + "  · merge         merge commit   allowed on main\n"
        + landing_row()
    )


def prompt(pull_request=7):
    return f"Switch quality_gate off, merge #{pull_request}, switch it on? Type yes: "


def not_confirmed_pair(pull_request=7, repository=REPO):
    return f"error: not confirmed · nothing changed\nnext: quality merge {repository}#{pull_request}\n"


def landing_pair(pull_request=7, repository=REPO):
    return (
        "error: not merged · the operator's merge ends the merge commit with the pull request body's one Work-Item: or No-Item: line · nothing changed\n"
        f"next: give the pull request body exactly one Work-Item: or No-Item: line outside code blocks, then run quality merge {repository}#{pull_request} again\n"
    )


def not_judged_pair():
    return (
        "error: could not judge · nothing changed\n"
        "next: fix what the ✗ row names, then run quality merge acme/widgets#7 again\n"
    )


def terminal_pair():
    return (
        "error: not merged · the operator's merge needs a terminal on stdin · nothing changed\n"
        "next: the operator runs it in his own terminal\n"
    )


def frozen_now():
    return datetime(2026, 11, 6, 12, 0, tzinfo=UTC)


def attempt_rows(method="merge commit"):
    """The four step rows of a merge that goes through, on `acme/widgets#7`."""
    return (
        "  ✓ quality_gate  off            acme/widgets\n"
        f"  ✓ #7            merged         {method}\n"
        "  ✓ quality_gate  on\n"
        "  ✓ log comment   posted         on #7\n"
    )


def closing_line():
    return "✓ merged #7 · quality_gate on again · <1s\n"


def restore_line():
    return '    echo \'{"properties":[{"property_name":"quality_gate","value":"true"}]}\' | gh api -X PATCH repos/acme/widgets/properties/values --input -\n'


def log_body(switched="off", outcome="merge", restored="on", method="merge commit", findings="4 legacy finding(s)"):
    return (
        f"body=quality merge run by @felix at 2026-11-06T12:00:00+00:00: {findings}; merges with a {method}; "
        f"quality_gate {switched} → {outcome} → {restored}."
    )


def comment_line(**arguments):
    return f"    gh api -X POST repos/acme/widgets/issues/7/comments -f '{log_body(**arguments)}'\n"


def drive(gh, transport=None, typed="yes\n", terminal=True, repo=REPO, when_asked=lambda: None, pull_request="7", clock=lambda: 0.0, words=()):
    """The `typed` argument is the line the terminal answers, its line end included (`""` the end of input); an exception is raised."""
    answers = []

    def read_line():
        when_asked()
        answers.append(typed)
        if isinstance(typed, BaseException):
            raise typed
        return typed

    remote = getattr(gh, "remote", None)
    code = cli.main(
        ["merge", f"{repo}#{pull_request}", *words], gh=gh, transport=transport or sonar(), terminal=cli.Terminal(read_line, lambda: terminal), remote_for=no_network if remote is None else lambda repository: remote,
        clock=clock, now=frozen_now,
    )
    return code, answers


def run(gh, capsys, **arguments):
    """The door's exit, stdout, stderr and the answers it read, on a run through `drive`."""
    code, answers = drive(gh, **arguments)
    streams = capsys.readouterr()
    return code, streams.out, streams.err, answers


def changed_nothing(gh):
    assert gh.gate_values == []
    assert gh.merge_calls == []
