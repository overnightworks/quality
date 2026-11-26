import pytest

from tests.support.merge import (
    B_REMOVED,
    OWNER,
    REPO,
    REVIEW_ID,
    SHA,
    FakeGh,
    changed_nothing,
    consent_block,
    consent_missing_pair,
    consent_missing_rows,
    consent_row,
    declared_loosening,
    log_body,
    review,
    rows_before_the_consent,
    run,
)

BLOCK = consent_block()
OTHER = "reviewer"
EARLIER_COMMIT = "c" * 40
MEMBERS = {OWNER: ("admin", "active"), OTHER: ("member", "active")}


def reviewed(*reviews, memberships=MEMBERS):
    return {"reviews": list(reviews), "memberships": memberships}


def owner_review(body=BLOCK, **fields):
    return review(body, **fields)


def a_later_review(body=BLOCK, **fields):
    return review(body, review_id=REVIEW_ID + 1, **fields)


MERGES = [
    pytest.param(reviewed(owner_review()), REVIEW_ID, id="an owner's latest review, a comment at the head with the printed text"),
    pytest.param(reviewed(owner_review(state="APPROVED")), REVIEW_ID, id="the same as an approval"),
    pytest.param(reviewed(owner_review(BLOCK.replace("\n", "\r\n"))), REVIEW_ID, id="the text with CRLF line endings"),
    pytest.param(reviewed(owner_review(BLOCK + "\n")), REVIEW_ID, id="the text with a trailing line end"),
    pytest.param(reviewed(owner_review(BLOCK + "\r\n")), REVIEW_ID, id="the text with a trailing CRLF"),
    pytest.param(
        reviewed(owner_review(commit_id=EARLIER_COMMIT), a_later_review()), REVIEW_ID + 1,
        id="a push after the first review and a new review at the head",
    ),
    pytest.param(
        reviewed(owner_review(), review("fine", OTHER, "APPROVED", review_id=9)), REVIEW_ID,
        id="a later review by a member who is no owner leaves the owner's standing",
    ),
]
REFUSALS = [
    pytest.param(reviewed(), id="no review at all"),
    pytest.param(reviewed(owner_review(login=OTHER)), id="the latest review by a member who is no owner"),
    pytest.param(reviewed(owner_review(login="stranger")), id="a review by a login that is no member"),
    pytest.param(reviewed(owner_review(), a_later_review(body="LGTM")), id="an earlier owner review matches and the latest differs"),
    pytest.param(reviewed(owner_review(state="PENDING")), id="the owner's review pending"),
    pytest.param(reviewed(owner_review(state="DISMISSED")), id="the owner's review dismissed"),
    pytest.param(reviewed(owner_review(state="CHANGES_REQUESTED")), id="the owner's review requesting changes"),
    pytest.param(reviewed(owner_review(commit_id=EARLIER_COMMIT)), id="the review at an earlier commit"),
    pytest.param(reviewed(owner_review(BLOCK.replace("main", "trunk"))), id="the text differs in one value"),
    pytest.param(reviewed(owner_review(BLOCK.strip("`\n"))), id="the text without its fence"),
    pytest.param(reviewed(owner_review(f"Please merge.\n{BLOCK}")), id="content before the block"),
    pytest.param(reviewed(owner_review(f"{BLOCK}\nThanks.")), id="content after the block"),
    pytest.param(reviewed(owner_review(consent_block(legacy_branch=None))), id="the block without the legacy line where Sonar failed"),
    pytest.param(reviewed(owner_review(None)), id="a review without a body"),
    pytest.param(
        reviewed(owner_review(), memberships={OWNER: ("admin", "pending")}), id="the latest review by an owner whose membership is pending",
    ),
]


@pytest.mark.parametrize("words", [[], ["--dry-run"]], ids=["a run", "a dry run"])
@pytest.mark.parametrize(("gh_kwargs", "review_id"), MERGES)
def test_the_door_passes_an_owners_latest_review_at_the_head_with_the_printed_text(gh_kwargs, review_id, words, capsys):
    """MERGE-119, MERGE-120, MERGE-122: an organisation owner's latest review at the head, a comment or an approval and
    not dismissed, whose text is the printed block line endings aside, is the consent: a run merges and a dry run
    shows the same row and merges nothing."""
    gh = FakeGh(**gh_kwargs)
    code, out, err = run(gh, capsys, words=words)
    assert (code, err) == (0, "")
    assert out.startswith(rows_before_the_consent() + consent_row(review_id))
    if words:
        changed_nothing(gh)
        assert gh.comments == []
    else:
        assert (len(gh.merge_calls), len(gh.comments)) == (1, 1)


@pytest.mark.parametrize("words", [[], ["--dry-run"]], ids=["a run", "a dry run"])
@pytest.mark.parametrize("gh_kwargs", REFUSALS)
def test_the_door_refuses_every_other_consent_with_the_text_and_the_pair(gh_kwargs, words, capsys):
    """MERGE-121, MERGE-122: without an owner's latest review at the head with exactly the text, the door shows the consent
    row refused, the block below it whole and the pair, changes nothing and asks no question, a dry run too."""
    gh = FakeGh(**gh_kwargs)
    assert run(gh, capsys, words=words) == (1, rows_before_the_consent() + consent_missing_rows(), consent_missing_pair())
    changed_nothing(gh)
    assert gh.comments == []


@pytest.mark.parametrize(
    ("gh_kwargs", "block"),
    [
        pytest.param({"reviews": []}, f"```\nquality merge consent for {REPO}#7 at {SHA}\nlegacy findings on main\n```", id="Sonar failed"),
        pytest.param(
            declared_loosening(reviews=[]),
            f"```\nquality merge consent for {REPO}#7 at {SHA}\n{B_REMOVED} · overnightworks/songmaker#1345 of 2026-10-20\nreason: ruled\n```",
            id="a declared loosening with Sonar green",
        ),
    ],
)
def test_the_block_is_the_fenced_header_the_legacy_line_and_each_declaration(gh_kwargs, block, capsys):
    """MERGE-119: the consent text is one fenced block: the header naming the pull request and its head, `legacy findings on
    <default branch>` where Sonar failed and each declared loosening with its reason."""
    code, out, _ = run(FakeGh(**gh_kwargs), capsys)
    assert (code, out.endswith(f"\n{block}\n")) == (1, True)


def test_a_reason_with_fences_a_mention_and_a_control_character_stays_inside_the_block_and_out_of_the_log_comment(capsys):
    """MERGE-119, MERGE-123: every line of the block carries its own words, a reason is escaped on its line so none closes the
    fence early or mentions anyone, and the log comment names the review and its commit and carries no declaration."""
    reason = "``` @someone\x1b[8m hidden ```"
    refused = run(FakeGh(**declared_loosening(reviews=[], reason=reason)), capsys)[1]
    block = refused[refused.index("\n```\nquality merge consent") + 1 :].splitlines()
    assert (block[0], block[-1], [line for line in block[1:-1] if set(line) <= {"`", " "}]) == ("```", "```", [])
    assert block[-2] == "reason: ``` @someone\\x1b[8m hidden ```"
    gh = FakeGh(**declared_loosening(reason=reason))
    assert run(gh, capsys)[0] == 0
    assert gh.comments == [log_body(findings="")]


def test_the_log_comment_stays_one_line_that_names_the_review_and_its_commit(capsys):
    """MERGE-123: the log comment names the consent review and the commit it was written at."""
    gh = FakeGh(reviews=[owner_review(review_id=77)], memberships=MEMBERS)
    assert run(gh, capsys)[0] == 0
    assert gh.comments == [log_body(review_id=77)]


@pytest.mark.parametrize("moment", ["reviews", "membership"])
def test_a_consent_that_cannot_be_read_is_not_judged_and_changes_nothing(moment, capsys):
    """MERGE-121: reviews or a membership GitHub does not answer are no consent and no refusal: the `✗ consent` row, exit 2."""
    gh = FakeGh(fail_on=[moment])
    code, out, _ = run(gh, capsys)
    assert (code, f"  ✗ consent       not judged     {moment} broke\n" in out) == (2, True)
    changed_nothing(gh)
