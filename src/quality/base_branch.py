"""What GitHub says about a pull request's base branch: the merge methods it leaves open and how its live tip compares with a head."""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from urllib.parse import quote

from quality.checkout import COMMIT_SHA
from quality.gh import GhFailed, GhRunner
from quality.report import CannotJudge


class MergeMethod(Enum):
    """The methods the door lands with, in its order of preference; the values are the pull request rule's method names
    and the merge request's `merge_method`. A merge commit keeps the judged head as a parent and a squash lands the judged
    merge ref's tree; a rebase replays commits, so the door never uses it."""

    MERGE = "merge"
    SQUASH = "squash"

    @property
    def settings_key(self) -> str:
        return {"merge": "allow_merge_commit", "squash": "allow_squash_merge"}[self.value]


RULE_METHOD_NAMES = ("merge", "squash", "rebase")


@dataclass(frozen=True)
class Comparison:
    """GitHub's comparison of the base branch's live tip with a head: the tip and how many of its commits the head lacks."""

    tip: str
    behind_by: int


def methods_the_settings_allow(repository_facts: Mapping[str, object]) -> frozenset[MergeMethod]:
    allowed = {method: repository_facts.get(method.settings_key) for method in MergeMethod}
    if not all(isinstance(value, bool) for value in allowed.values()):
        raise CannotJudge("the repository settings withhold or malform which merge methods they allow")
    return frozenset(method for method, value in allowed.items() if value)


def methods_the_rule_allows(rule: object, base: str) -> frozenset[MergeMethod] | None:
    """The methods one branch rule leaves open, None where it restricts none."""
    malformed = CannotJudge(f"the branch rules of {base} are malformed")
    if not isinstance(rule, Mapping) or not isinstance(rule.get("type"), str):
        raise malformed
    if "parameters" in rule and not isinstance(rule["parameters"], Mapping):
        raise malformed
    if rule["type"] == "required_linear_history":
        return frozenset(MergeMethod) - {MergeMethod.MERGE}
    if rule["type"] != "pull_request" or "parameters" not in rule:
        return None
    parameters = rule["parameters"]
    if "allowed_merge_methods" not in parameters:
        return None
    named = parameters["allowed_merge_methods"]
    if not isinstance(named, list) or not all(isinstance(name, str) and name in {*RULE_METHOD_NAMES} for name in named):
        raise malformed
    return frozenset(method for method in MergeMethod if method.value in named)


def read_branch_rules(gh: GhRunner, repository: str, base: str) -> list[object]:
    malformed = CannotJudge(f"the branch rules of {base} are malformed")
    try:
        listed = gh(["api", "--paginate", "--slurp", f"repos/{repository}/rules/branches/{quote(base, safe='/')}?per_page=100"], None)
    except GhFailed as error:
        raise CannotJudge(f"cannot read the branch rules of {base}: {error}") from error
    try:
        pages = json.loads(listed)
    except ValueError as error:
        raise malformed from error
    if not isinstance(pages, list) or not all(isinstance(page, list) for page in pages):
        raise malformed
    return [rule for page in pages for rule in page]


def choose_merge_method(gh: GhRunner, repository: str, repository_facts: Mapping[str, object], base: str) -> MergeMethod:
    """The door's method: a merge commit, else a squash, among those the settings and every rule on `base` allow."""
    allowed = methods_the_settings_allow(repository_facts)
    for rule in read_branch_rules(gh, repository, base):
        left_open = methods_the_rule_allows(rule, base)
        allowed = allowed if left_open is None else allowed & left_open
    chosen = next((method for method in MergeMethod if method in allowed), None)
    if chosen is None:
        raise CannotJudge("the repository and its branch rules allow no merge method the door uses (a merge commit or a squash)")
    return chosen


def compare_with_tip(gh: GhRunner, repository: str, base: str, head_sha: str) -> Comparison:
    """The live tip of `refs/heads/<base>` (a tag of the same name cannot answer) and the commits of it that `head_sha`
    lacks, one commit per page so the answer holds neither commits nor files; an answer that is no such count and
    commit sha cannot be judged."""
    unreadable = f"cannot compare {head_sha[:7]} with {base}"
    try:
        answer = gh(["api", f"repos/{repository}/compare/refs/heads/{quote(base, safe='/')}...{head_sha}?per_page=1"], None)
    except GhFailed as error:
        raise CannotJudge(f"{unreadable}: {error}") from error
    try:
        facts = json.loads(answer)
    except ValueError as error:
        raise CannotJudge(f"{unreadable}: GitHub's answer is no JSON") from error
    behind_by = facts.get("behind_by") if isinstance(facts, Mapping) else None
    commit = facts.get("base_commit") if isinstance(facts, Mapping) else None
    tip = commit.get("sha") if isinstance(commit, Mapping) else None
    if isinstance(behind_by, bool) or not isinstance(behind_by, int) or behind_by < 0 or not (isinstance(tip, str) and COMMIT_SHA.fullmatch(tip)):
        raise CannotJudge(f"{unreadable}: GitHub's answer holds no count of commits behind and no tip commit")
    return Comparison(tip, behind_by)
