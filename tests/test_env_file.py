import os
import shutil
import subprocess
from pathlib import Path

import pytest

from quality.report import FindingEvent
from quality.rules import Rule
from tests.support.check import assert_row_prints_finding, rows_of, rule_findings
from tests.support.check import hygiene_output as run

TRACKED_NAMES = [".env", "config/prod.env", ".env.local"]
IGNORED_NAMES = ["docs/.env.example", "docs/docker/.env.docker.example", "docs/environment.txt"]


def test_the_rule_finds_the_tracked_env_files_and_no_template(build_repo):
    """#3 rule 2/3: a tracked env file is found as `tracked`; templates pass."""
    findings = rule_findings(build_repo({name: "A=1\n" for name in [*TRACKED_NAMES, *IGNORED_NAMES]}), Rule.ENV_FILE)
    assert [(finding.path, finding.event) for finding in findings] == [(name, FindingEvent.TRACKED) for name in sorted(TRACKED_NAMES)]


def test_the_door_prints_each_tracked_env_file_once_as_the_rule_found_it(build_repo, capsys):
    repo = build_repo({name: "A=1\n" for name in [*TRACKED_NAMES, *IGNORED_NAMES]})
    code, output = run(capsys, repo)
    assert code == 1
    for finding in rule_findings(repo, Rule.ENV_FILE):
        (row,) = rows_of(output, finding)
        assert_row_prints_finding(row, finding)


def test_a_repository_without_a_tracked_env_file_passes(build_repo, capsys):
    assert run(capsys, build_repo({name: "A=1\n" for name in IGNORED_NAMES}))[0] == 0


def test_untracked_ignored_env_file_is_not_a_finding(build_repo, capsys):
    """#3 rule 2/3: an untracked, ignored env file is not a finding."""
    repo = build_repo({".gitignore": ".env\n"})
    (repo / ".env").write_text("A=1\n")
    assert run(capsys, repo)[0] == 0


def test_a_tracked_env_file_deleted_from_disk_is_still_a_finding(build_repo, capsys):
    """#76 rule 5, cell 14 (E5): the rule judges the index's names, so only `git rm --cached` clears it."""
    repo = build_repo({".env": "A=1\n"})
    (repo / ".env").unlink()
    code, output = run(capsys, repo)
    assert code == 1
    (finding,) = rule_findings(repo, Rule.ENV_FILE)
    assert len(rows_of(output, finding)) == 1


SHELLS = ["dash", "bash"]


def split_fix(fix: str) -> tuple[str, str]:
    """The command and the rest of a fix, divided at its first `, ` outside shell quotes, single or double (a shell word holds a space only inside them)."""
    open_quote = None
    for position, character in enumerate(fix):
        if open_quote is None and fix[position:position + 2] == ", ":
            return fix[:position], fix[position + 2:]
        if character in "'\"" and open_quote in (None, character):
            open_quote = None if open_quote else character
    raise AssertionError(fix)


def tracked_names(repo: Path) -> set[bytes]:
    listing = subprocess.run(["git", "-C", str(repo), "ls-files", "-z"], check=True, capture_output=True).stdout
    return {name for name in listing.split(b"\0") if name}


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(
    ("target", "neighbours", "printed_word"),
    [
        (".env", [], ".env"),
        ("config/prod.env", [], "config/prod.env"),
        ("my file.env", [], "'my file.env'"),
        ("sub dir/.env.prod", [], "'sub dir/.env.prod'"),
        ("it's.env", [], "'it'\"'\"'s.env'"),
        ("grün.env", [], "'grün.env'"),
        ("-x.env", [], "-x.env"),
        (":x.env", ["x.env"], ":x.env"),
        ("*.env", ["a.env"], "'*.env'"),
        ("[x].env", ["x.env"], "'[x].env'"),
        ("100%.env", [], "100%.env"),
        ("back\\slash.env", [], "'back\\slash.env'"),
        ("=x.env", [], "'=x.env'"),
    ],
    ids=[
        "#86 cell 1 bare name", "#86 cell 1 nested name", "#86 cell 2 space", "#86 cell 2 space in directory",
        "#86 cell 2 apostrophe", "#86 cell 2 umlaut", "#86 cell 3 leading dash", "#86 cell 4 colon beside the plain name",
        "#86 cell 4 star beside a.env", "#86 cell 4 brackets beside x.env", "#86 cell 5 percent", "#86 cell 5 backslash",
        "#86 cell 6 leading equals",
    ],
)
def test_the_printed_env_fix_untracks_exactly_the_named_file_in_every_shell(
    build_repo, capsys, shell, target, neighbours, printed_word
):
    """#86 rule 2, E1-E3 and E5: the command as printed, run from the top, untracks that file and leaves it on disk."""
    shell_path = shutil.which(shell)
    if shell_path is None:
        pytest.skip(f"{shell} is not installed")
    repo = build_repo({name: "A=1\n" for name in [target, *neighbours]})
    before = tracked_names(repo)
    _, output = run(capsys, repo)
    (finding,) = (found for found in rule_findings(repo, Rule.ENV_FILE) if found.path == target)
    (row,) = rows_of(output, finding)
    command, rest = split_fix(row.fix)
    assert command.endswith(f" {printed_word}")
    subprocess.run([shell_path, "-c", command], cwd=repo, check=True, capture_output=True)
    assert tracked_names(repo) == before - {os.fsencode(target)}
    assert (repo / target).exists()
    if printed_word == target:
        assert target in rest.split()
        (repo / ".gitignore").write_text(f"{target}\n")
        ignored = subprocess.run(
            ["git", "check-ignore", "--no-index", "--stdin", "-z"], cwd=repo, check=True, capture_output=True, text=True, input=f"./{target}\0"
        )
        # check-ignore reads a leading `:` as pathspec magic, also on stdin, so the path goes in relative.
        assert ignored.stdout.rstrip("\0").removeprefix("./") == target
    else:
        assert target not in rest
        assert printed_word not in rest


def test_an_unprintable_env_name_gets_a_hint_and_never_a_command(build_repo, capsys):
    """#86 rule 2, E4: the same hint for every such name, with no command; #97 rule 2 prints the name escaped."""
    names = ["lat\udce9.env", "line\nfeed.env", "esc\x1bape.env", "c1\x85.env", "del\x7f.env", "rlo\u202e.env", "zwsp\u200b.env"]
    repo = build_repo({name: "A=1\n" for name in names})
    _, output = run(capsys, repo)
    findings = rule_findings(repo, Rule.ENV_FILE)
    assert len(findings) == len(names)
    assert len({finding.fix for finding in findings}) == 1
    for finding in findings:
        (row,) = rows_of(output, finding)
        assert_row_prints_finding(row, finding)
        assert finding.path not in row.fix
        assert finding.location not in row.fix
        assert not {"rm", "--cached"} & set(row.fix.split())
