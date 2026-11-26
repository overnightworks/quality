"""The operator's consent to a merge: the text the door prints for a head, and the organisation owner's review that carries it."""

import json
from collections.abc import Sequence
from dataclasses import dataclass

from quality.config import Declaration
from quality.gh import GhFailed, GhRunner
from quality.report import CannotJudge, printable
from quality.verdict import declaration_texts

FENCE = "```"
OWNER_ROLE = "admin"
CONSENTING_STATES = frozenset({"COMMENTED", "APPROVED"})


@dataclass(frozen=True)
class Review:
    """One review of a pull request as GitHub lists it; `body` is empty where GitHub holds none."""

    id: int
    login: str
    state: str
    commit_id: str
    body: str


def consent_lines(repository: str, pull_request: int, head_sha: str, declared: Sequence[Declaration], legacy_branch: str | None) -> tuple[str, ...]:
    """The consent text the door prints for a head, one fenced block: each line escaped and starting with the door's own
    words, so no value closes the fence early or moves to another line; `legacy_branch` is the default branch where
    Sonar failed."""
    legacy = () if legacy_branch is None else (f"legacy findings on {legacy_branch}",)
    loosenings = (printable(text) for declaration in declared for text in declaration_texts(declaration))
    return (FENCE, f"quality merge consent for {repository}#{pull_request} at {head_sha}", *legacy, *loosenings, FENCE)


def parse_review(each: object) -> Review:
    """The review as GitHub lists it; any other shape is not judged."""
    if not isinstance(each, dict) or not isinstance(each.get("user") or {}, dict) or not isinstance(each.get("body") or "", str):
        raise CannotJudge("GitHub's answer for the pull request's reviews is not a list of reviews")
    return Review(each["id"], (each["user"] or {}).get("login", ""), each["state"], each["commit_id"], each["body"] or "")


def read_reviews(gh: GhRunner, repository: str, pull_request: int) -> list[Review]:
    listed = gh(["api", "--paginate", f"repos/{repository}/pulls/{pull_request}/reviews?per_page=100", "--jq", ".[]"], None)
    return [parse_review(json.loads(line)) for line in listed.splitlines()]


def is_organisation_owner(gh: GhRunner, repository: str, login: str) -> bool:
    """Whether `login` is an active owner of the organisation holding `repository`; a login that is no member is none."""
    organisation = repository.partition("/")[0]
    try:
        membership = json.loads(gh(["api", f"orgs/{organisation}/memberships/{login}"], None))
    except GhFailed as error:
        if error.not_found:
            return False
        raise
    if not isinstance(membership, dict):
        raise CannotJudge("GitHub's answer for the organisation membership is not a membership")
    return membership["role"] == OWNER_ROLE and membership["state"] == "active"


def line_ends_aside(text: str) -> str:
    """CRLF read as LF and the trailing line ends dropped."""
    return text.replace("\r\n", "\n").rstrip("\n")


def read_consent(gh: GhRunner, repository: str, pull_request: int, head_sha: str, lines: tuple[str, ...]) -> Review | None:
    """The review that consents to `lines` at `head_sha`: an organisation owner's latest review, submitted as a comment or an
    approval and not dismissed, made at that head and holding exactly the text, line endings aside; None where none does."""
    latest = {review.login: review for review in read_reviews(gh, repository, pull_request) if review.login}
    text = "\n".join(lines)
    holding_the_text = (
        review for review in latest.values()
        if review.state in CONSENTING_STATES and review.commit_id == head_sha and line_ends_aside(review.body) == text
    )
    return next((review for review in holding_the_text if is_organisation_owner(gh, repository, review.login)), None)
