import pytest

from quality.config import CONFIG_PATH
from quality.ratchet import BASELINE_PATH
from tests.support.architecture import FORBIDDEN, GUARDED_CHANGES, contracts_toml
from tests.support.git_repositories import put
from tests.support.merge import (
    B_REMOVED,
    ONE_JOB,
    SONAR_GREEN,
    TWO_JOBS,
    changed_nothing,
    closing_line,
    declaring,
    drive,
    not_judged_pair,
)
from tests.support.processes import process_is_gone
from tests.support.verdict import declaration, gate_config, verdict_of, verdict_streams, viewed
from tests.support.verdict_renames import MOVABLE_FILE, OLD_ENTRY, commit_all, file_text, pulled

STRUCK_FIELD = "source_modules"
DEAD = {**FORBIDDEN, "source_modules": ["shop.db", "shop.dead"]}
STRUCK = {**FORBIDDEN, "source_modules": ["shop.db"]}
STRUCK_WITH_NEW = {**FORBIDDEN, "source_modules": ["shop.db", "shop.new"]}
SHOP_BASE = {
    "src/shop/__init__.py": "", "src/shop/web.py": "", "src/shop/db.py": "x = 1\n", "src/shop/dead.py": MOVABLE_FILE,
}
CHANGES = f"    ! {GUARDED_CHANGES}{STRUCK_FIELD}"
GREEN = ["✓ green"]


def committed(pull_request, base_contract, head_contract, head_files, baselines=(None, None), base_files=None, also=None):
    """The base commit holds the shop, `base_files`, the base contract and the first of `baselines`; the head commit
    changes the files and the contract and holds the second; `also` maps the name of a second contract to its base and
    head options."""
    also = also or {}
    repo, commit_base = pull_request
    base_baseline, head_baseline = baselines
    for name, text in {**SHOP_BASE, **(base_files or {}), "pyproject.toml": contracts_toml({"guarded": base_contract, **{name: before for name, (before, _) in also.items()}})}.items():
        put(repo / name, text)
    commit_base(base_baseline)
    for name, text in {**head_files, "pyproject.toml": contracts_toml({"guarded": head_contract, **{name: after for name, (_, after) in also.items()}}),
                       BASELINE_PATH: head_baseline}.items():
        put(repo / name, text)
    commit_all(repo, "head")
    return repo


DELETED = {"src/shop/dead.py": None}
RENAMED = {"src/shop/dead.py": None, "src/shop/new.py": MOVABLE_FILE}
REWRITTEN = "".join(f"nothing of the old file survives in line {number}\n" for number in range(30))
EDITED = {"src/shop/dead.py": None, "src/shop/new.py": MOVABLE_FILE.replace("line 3 ", "edited line 3 ").replace("line 4 ", "edited line 4 ")}
REWRITTEN_AND_MOVED = {"src/shop/dead.py": None, "src/shop/new.py": REWRITTEN}
LISTED_ELSEWHERE = {**FORBIDDEN, "source_modules": ["shop.db"], "forbidden_modules": ["shop.web", "shop.new"]}
OTHER_CONTRACT = {"type": "forbidden", "source_modules": ["shop.api"], "forbidden_modules": ["shop.web"]}
LISTED_IN_OTHER_CONTRACT = {**OTHER_CONTRACT, "source_modules": ["shop.api", "shop.new"]}
NOT_UTF8_FOLDER = {"src/shop/dead.py": None, "src/shop/dead/__init__.py": None, "src/shop/dead/\udcff.py": "x = 2\n"}


@pytest.mark.integration
@pytest.mark.parametrize(
    ("head_contract", "head_files", "code", "expected"),
    [
        (STRUCK, DELETED, 0, GREEN),
        (STRUCK, {}, 1, [CHANGES, "! red"]),
        (STRUCK, RENAMED, 1, [CHANGES, "! red"]),
        (STRUCK_WITH_NEW, RENAMED, 0, GREEN),
        (STRUCK, NOT_UTF8_FOLDER, 1, [CHANGES, "! red"]),
    ],
    ids=["a deleted module", "a kept module", "a renamed module", "a renamed module whose new name is listed",
         "a folder holding a file whose name is not UTF-8"],
)
def test_the_verdict_reads_the_merge_commits_files_before_a_strike_loosens(capsys, pull_request, head_contract, head_files, code, expected):
    """LOOSE-19, LOOSE-20, LOOSE-21, LOOSE-22, LOOSE-24: a struck module the merge commit no longer has, or git reads as
    renamed to a module the same list names, is green; one it keeps, one renamed and not listed again and one whose
    folder still holds a file stay `changes <list>`."""
    repo = committed(pull_request, DEAD, head_contract, head_files)
    assert viewed(repo, capsys) == (code, expected)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("head_contract", "head_files", "also", "code", "expected"),
    [
        (STRUCK_WITH_NEW, EDITED, None, 0, GREEN),
        (STRUCK, EDITED, None, 1, [CHANGES, "! red"]),
        (STRUCK_WITH_NEW, REWRITTEN_AND_MOVED, None, 0, GREEN),
        (STRUCK, REWRITTEN_AND_MOVED, None, 0, GREEN),
        (LISTED_ELSEWHERE, RENAMED, None, 1, [CHANGES, "! red"]),
        (STRUCK, RENAMED, {"other": (OTHER_CONTRACT, LISTED_IN_OTHER_CONTRACT)}, 1, [CHANGES, "! red"]),
    ],
    ids=["a module moved with a few lines edited, listed", "a module moved with a few lines edited, unlisted",
         "a module rewritten below the similarity threshold, listed", "a module rewritten below the similarity threshold, unlisted",
         "the new name listed in another list of the contract", "the new name listed in another contract"],
)
def test_the_verdict_clears_a_renamed_strike_only_for_the_pairing_git_makes_and_the_list_that_names_the_new_module(
    capsys, pull_request, head_contract, head_files, also, code, expected
):
    """LOOSE-21, LOOSE-22: git pairs a moved module above its similarity threshold, so only the same list naming its new
    name clears the strike; a module rewritten below it reads as deleted and added, whatever the head lists."""
    repo = committed(pull_request, DEAD, head_contract, head_files, also=also)
    assert viewed(repo, capsys) == (code, expected)


MOVED_PATHS = ("src/old.py", "src/new.py")
MOVED_BASELINES = (OLD_ENTRY, OLD_ENTRY.replace(*MOVED_PATHS))


@pytest.mark.integration
def test_the_verdict_carries_a_baseline_entry_under_the_renames_of_the_tree_a_strike_reads(capsys, pull_request):
    """LOOSE-24: beside a dead strike, a baseline entry whose file the head renames is carried, not read as raised."""
    old, new = MOVED_PATHS
    moved = file_text(old)
    repo = committed(pull_request, DEAD, STRUCK, {**DELETED, old: None, new: moved}, MOVED_BASELINES, {old: moved})
    assert viewed(repo, capsys) == (0, GREEN)


@pytest.mark.integration
def test_a_declaration_for_a_strike_the_files_make_green_matches_nothing(capsys, pull_request):
    """LOOSE-19: a declared strike the head makes green is an unmatched declaration, red."""
    repo, commit_base = pull_request
    declared = declaration(f"{GUARDED_CHANGES}{STRUCK_FIELD}")
    for name, text in {**SHOP_BASE, "pyproject.toml": contracts_toml({"guarded": DEAD})}.items():
        put(repo / name, text)
    commit_base(None)
    put(repo / "src/shop/dead.py", None)
    put(repo / "pyproject.toml", contracts_toml({"guarded": STRUCK}))
    put(repo / CONFIG_PATH, gate_config("checks", declared=(declared,)))
    commit_all(repo, "head")
    code, output = verdict_of(repo, capsys)
    assert (code, "unmatched" in output) == (1, True)


@pytest.mark.parametrize(
    ("stalls", "fact"),
    [
        ("ls-tree -r", "cannot list the files git holds at the pull request's commits"),
        ("diff", "cannot list the files git renamed between the pull request's commits"),
    ],
    ids=["the file listing", "the rename listing"],
)
def test_the_door_cannot_judge_a_strike_whose_listing_outlasts_the_limit_and_leaves_no_child(
    capsys, tmp_path, monkeypatch, fake_git, stalls, fact
):
    """LOOSE-24: a tree read that outlasts the door's one limit is exit 2 in the door's words, and its process group dies."""
    monkeypatch.setattr("quality.sides.FETCH_TIMEOUT_SECONDS", 0.5)
    child = tmp_path / "child-pid"
    gh = struck_pull(tmp_path, STRUCK, DELETED, **DECLARED_JOB_REMOVAL)
    fake_git(f'case "$*" in *" {stalls} "*) sleep 300 & echo $! > "{child}"; wait;; esac')
    code, _ = drive(gh)
    streams = capsys.readouterr()
    assert (code, fact in streams.out, streams.err) == (2, True, not_judged_pair())
    assert process_is_gone(int(child.read_text()))
    changed_nothing(gh)


@pytest.mark.integration
def test_a_tree_git_cannot_list_cannot_be_judged_once_a_module_is_struck(capsys, pull_request, fake_git):
    """LOOSE-24: a refused listing is exit 2 naming the commit."""
    repo = committed(pull_request, DEAD, STRUCK, DELETED)
    fake_git('case "$*" in *" ls-tree -r "*) exit 1;; esac')
    code, output = verdict_of(repo, capsys)
    assert (code, "error: cannot list the files tracked at " in output) == (2, True)


@pytest.mark.integration
def test_a_verdict_without_a_struck_module_asks_git_for_no_listing(capsys, pull_request, fake_git):
    """LOOSE-24: with no module struck no listing is asked."""
    repo = committed(pull_request, DEAD, DEAD, DELETED)
    fake_git('case "$*" in *" ls-tree -r "*) exit 1;; esac')
    assert viewed(repo, capsys) == (0, GREEN)


@pytest.mark.integration
def test_a_rename_list_that_came_with_a_warning_cannot_be_judged_once_a_module_is_struck(capsys, pull_request, fake_git):
    """LOOSE-24: git prints a warning when it skips its rename search; the strike is exit 2, never green."""
    repo = committed(pull_request, DEAD, STRUCK, DELETED)
    fake_git('case "$*" in *" diff "*) echo "warning: exhaustive rename detection was skipped" >&2;; esac')
    code, output = verdict_of(repo, capsys)
    assert (code, "may miss renames" in output, "exhaustive" in output) == (2, True, False)


@pytest.mark.integration
def test_a_rename_list_git_cannot_produce_cannot_be_judged_once_a_module_is_struck_without_baselines(capsys, pull_request, fake_git):
    """LOOSE-24: a failed rename read is exit 2 although no baseline is compared."""
    repo = committed(pull_request, DEAD, STRUCK, DELETED)
    fake_git('case "$*" in *" diff "*) exit 1;; esac')
    code, _, err = verdict_streams(repo, capsys)
    assert (code, "cannot list the files renamed between" in err) == (2, True)


def struck_pull(tmp_path, head_contract, merge_files, moves=(), baselines=(None, None), **options):
    """A door run whose base holds the shop, the dead contract and the first of `baselines` and whose merge commit holds
    `head_contract` and the second."""
    gh = pulled(tmp_path, *baselines, moves, base_files=SHOP_BASE, merge_files=merge_files, **options)
    gh.files[("pyproject.toml", gh.base_sha)] = contracts_toml({"guarded": DEAD})
    gh.files[("pyproject.toml", gh.merge_sha)] = contracts_toml({"guarded": head_contract})
    return gh


DECLARED_JOB_REMOVAL = {"checks": SONAR_GREEN, "base_config": TWO_JOBS, "merge_config": ONE_JOB + declaring(B_REMOVED)}


@pytest.mark.integration
def test_the_door_merges_a_dead_strike_beside_a_declared_loosening_with_no_line_for_the_strike(capsys, tmp_path):
    """LOOSE-19, LOOSE-24: the door reads the pull request's merge commit; a deleted module's strike needs no declaration."""
    gh = struck_pull(tmp_path, STRUCK, DELETED, **DECLARED_JOB_REMOVAL)
    code, _ = drive(gh)
    out = capsys.readouterr().out
    assert (code, closing_line() in out, "source_modules" in out, len(gh.merge_calls)) == (0, True, False, 1)


@pytest.mark.integration
def test_the_door_carries_a_baseline_entry_under_the_renames_of_the_tree_a_strike_reads(capsys, tmp_path):
    """LOOSE-24: beside a dead strike, a baseline entry whose file the merge commit renames is carried and the door merges."""
    gh = struck_pull(tmp_path, STRUCK, DELETED, [MOVED_PATHS], MOVED_BASELINES, **DECLARED_JOB_REMOVAL)
    code, _ = drive(gh)
    out = capsys.readouterr().out
    assert (code, closing_line() in out, len(gh.merge_calls)) == (0, True, 1)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("merge_files", "moves"),
    [({}, ()), ({}, (("src/shop/dead.py", "src/shop/new.py"),))],
    ids=["a kept module", "a renamed module"],
)
def test_the_door_refuses_an_undeclared_strike_of_a_module_the_merge_commit_keeps_or_git_reads_as_renamed(capsys, tmp_path, merge_files, moves):
    """LOOSE-20, LOOSE-21: the door says `changes <list>` and merges nothing."""
    gh = struck_pull(tmp_path, STRUCK, merge_files, moves)
    code, _ = drive(gh)
    out = capsys.readouterr().out
    assert (code, f"{GUARDED_CHANGES}{STRUCK_FIELD}" in out, gh.merge_calls) == (1, True, [])


RENAME_MOVE = (("src/shop/dead.py", "src/shop/new.py"),)


@pytest.mark.integration
def test_the_door_merges_a_renamed_strike_whose_new_name_the_merge_commit_lists_with_no_line_for_the_strike(capsys, tmp_path):
    """LOOSE-22, LOOSE-24: beside a declared loosening, the renamed module in the same list leaves no line for the strike."""
    gh = struck_pull(tmp_path, STRUCK_WITH_NEW, {}, RENAME_MOVE, **DECLARED_JOB_REMOVAL)
    code, _ = drive(gh)
    out = capsys.readouterr().out
    assert (code, closing_line() in out, "source_modules" in out, len(gh.merge_calls)) == (0, True, False, 1)


@pytest.mark.integration
@pytest.mark.parametrize("head_contract", [STRUCK_WITH_NEW, STRUCK], ids=["listed", "unlisted"])
def test_the_verdict_and_the_door_print_the_same_loosening_lines_for_the_same_two_commits(
    capsys, tmp_path, run_env, monkeypatch, head_contract
):
    """LOOSE-22, LOOSE-24: one `git_pull` builds the base and merge commits once; the verdict reads its working
    repository, the door its remote, and both name the same loosenings."""
    files = {"quality/config.toml": '[gate]\nmandatory_jobs = ["checks"]\n'}
    gh = pulled(tmp_path, None, None, RENAME_MOVE, base_files={**SHOP_BASE, **files, "pyproject.toml": contracts_toml({"guarded": DEAD})},
                merge_files={"pyproject.toml": contracts_toml({"guarded": head_contract})}, **DECLARED_JOB_REMOVAL)
    gh.files[("pyproject.toml", gh.base_sha)] = contracts_toml({"guarded": DEAD})
    gh.files[("pyproject.toml", gh.merge_sha)] = contracts_toml({"guarded": head_contract})
    monkeypatch.setenv("GATE_BASE_SHA", gh.base_sha)
    verdict_code, verdict_out = verdict_of(tmp_path / "work", capsys)
    door_code, _ = drive(gh)
    door_out = capsys.readouterr().out
    named = [line.strip() for line in verdict_out.splitlines() if GUARDED_CHANGES in line]
    assert named == [line.strip() for line in door_out.splitlines() if GUARDED_CHANGES in line]
    assert bool(named) == (head_contract is STRUCK)
    expected_code = 1 if head_contract is STRUCK else 0
    assert verdict_code == expected_code
    assert door_code == expected_code


@pytest.mark.integration
def test_the_door_asks_for_no_remote_without_a_strike_and_without_baselines(tmp_path):
    """LOOSE-24: nothing is fetched when no module is struck."""
    gh = pulled(tmp_path, None, None, base_files=SHOP_BASE, checks=SONAR_GREEN, base_config=TWO_JOBS,
                merge_config=ONE_JOB + declaring(B_REMOVED))
    gh.files[("pyproject.toml", gh.base_sha)] = contracts_toml({"guarded": DEAD})
    gh.files[("pyproject.toml", gh.merge_sha)] = contracts_toml({"guarded": DEAD})
    gh.remote = None
    assert (drive(gh)[0], len(gh.merge_calls)) == (0, 1)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("fact", "break_it"),
    [
        ("cannot fetch the pull request's commits to compare renamed files (git exited 128)",
         lambda gh, tmp_path, fake_git: setattr(gh, "remote", str(tmp_path / "missing.git"))),
        ("cannot list the files git holds at the pull request's commits",
         lambda gh, tmp_path, fake_git: fake_git('case "$*" in *" ls-tree -r "*) exit 1;; esac')),
        ("cannot list the files git renamed between the pull request's commits",
         lambda gh, tmp_path, fake_git: fake_git('case "$*" in *" diff "*) exit 1;; esac')),
        ("git's list of renamed files between the pull request's commits came with a warning, so it may miss renames",
         lambda gh, tmp_path, fake_git: fake_git('case "$*" in *" diff "*) echo warning >&2;; esac')),
    ],
    ids=["the fetch", "the file listing", "the rename listing", "a warning on the rename listing"],
)
def test_the_door_cannot_judge_a_strike_whose_files_it_cannot_read(capsys, tmp_path, fake_git, fact, break_it):
    """LOOSE-24: a failed fetch, listing or rename list, or one with a warning, is exit 2 in the door's words."""
    gh = struck_pull(tmp_path, STRUCK, DELETED, **DECLARED_JOB_REMOVAL)
    break_it(gh, tmp_path, fake_git)
    code, _ = drive(gh)
    streams = capsys.readouterr()
    assert (code, fact in streams.out, streams.err) == (2, True, not_judged_pair())
    changed_nothing(gh)
