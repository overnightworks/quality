import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.support.hygiene import assert_refusal
from tests.support.hygiene import hygiene_output as run


@pytest.mark.parametrize(
    ("name", "refused"),
    [
        (".env", True),
        ("config/prod.env", True),
        (".env.local", True),
        ("docs/.env.example", False),
        ("docs/docker/.env.docker.example", False),
        ("docs/environment.txt", False),
    ],
)
def test_tracked_env_files(build_repo, capsys, name, refused):
    """#3 rule 2/3: a tracked env file is refused with rule id, path, reason and fix; templates pass."""
    code, output = run(capsys, build_repo({name: "A=1\n"}))
    assert code == (1 if refused else 0)
    if refused:
        assert_refusal(output, "env-file", name)


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
    assert_refusal(output, "env-file", ".env")


UNPRINTABLE_NAME_HINT = "remove it from the index by its bytes (git ls-files -z shows them), then add it to .gitignore."
FIX_SUFFIX = " and add it to .gitignore."
SHELLS = ["dash", "bash"]


def printed_fix(output: str, shown_name: str) -> str:
    """The command the `env-file` finding for `shown_name` prints after `Fix: `, without the closing sentence."""
    fix = re.search(rf"^env-file {re.escape(shown_name)} - .*\n  Fix: (.*)$", output, re.MULTILINE)
    assert fix, output
    assert fix.group(1).endswith(FIX_SUFFIX), output
    return fix.group(1).removesuffix(FIX_SUFFIX)


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
    fix = printed_fix(output, target)
    assert fix == f"git --literal-pathspecs rm --cached -- {printed_word}"
    subprocess.run([shell_path, "-c", fix], cwd=repo, check=True, capture_output=True)
    assert tracked_names(repo) == before - {os.fsencode(target)}
    assert (repo / target).exists()


@pytest.mark.parametrize(
    "name",
    ["lat\udce9.env", "line\nfeed.env", "esc\x1bape.env", "c1\x85.env", "del\x7f.env"],
    ids=["#86 cell 7 invalid UTF-8", "#86 cell 8 line feed", "#86 cell 8 escape", "#86 cell 8 C1 control", "#86 cell 8 DEL"],
)
def test_an_unprintable_env_name_gets_a_hint_and_never_a_command(build_repo, capsys, name):
    """#86 rule 2, E4: one hint line; #97 rule 2 prints the name on the finding line escaped."""
    _, output = run(capsys, build_repo({name: "A=1\n"}))
    assert f"\n  Fix: {UNPRINTABLE_NAME_HINT}\n" in output
    assert "rm --cached" not in output
