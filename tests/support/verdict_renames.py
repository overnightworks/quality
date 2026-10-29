import json
import subprocess

from quality.config import CONFIG_PATH
from quality.ratchet import BASELINE_PATH
from tests.support.git_repositories import GIT_IDENTITY, git_pull, put
from tests.support.merge import FakeGh

MOVABLE_FILE = "".join(f"line {number} of a file git can match to its move\n" for number in range(30))


def commit_all(repo, message):
    git = ["git", "-C", str(repo), *GIT_IDENTITY]
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", message], check=True)


def moved_in_head(
    pull_request, base, head, new_text=MOVABLE_FILE, old="src/old.py", new="src/new.py", head_config=None, base_files=None,
    head_files=None,
):
    """The base commit holds `old` and `base`; the head commit moves it to `new` (holding `new_text`) and holds `head`.

    `base_files` and `head_files` add files to either commit, a head file after the move."""
    repo, commit_base = pull_request
    put(repo / old, MOVABLE_FILE)
    for path, text in (base_files or {}).items():
        put(repo / path, text)
    commit_base(base)
    (repo / new).parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(repo), "mv", old, new], check=True)
    put(repo / new, new_text)
    put(repo / BASELINE_PATH, head)
    if head_config is not None:
        put(repo / CONFIG_PATH, head_config)
    for path, text in (head_files or {}).items():
        put(repo / path, text)
    commit_all(repo, "head moves the file")


def file_text(name):
    return "".join(f"{name} row {number} that no other file shares\n" for number in range(30))


def moves_in_head(pull_request, base, head, moves):
    """The base commit holds every old path and `base`; the head commit moves each (old, new) and holds `head`."""
    repo, commit_base = pull_request
    for old, _ in moves:
        put(repo / old, file_text(old))
    commit_base(base)
    for old, new in moves:
        subprocess.run(["git", "-C", str(repo), "mv", old, new], check=True)
    put(repo / BASELINE_PATH, head)
    commit_all(repo, "head moves the files")


def pulled(directory, base_baseline, merge_baseline, moves=(), **options):
    """A FakeGh whose pull request has real base and merge commits in a local remote."""
    remote, base_sha, merge_sha = git_pull(directory, base_baseline, merge_baseline, moves)
    return FakeGh(
        base_baseline=base_baseline, merge_baseline=merge_baseline, base_sha=base_sha, merge_sha=merge_sha, remote=remote,
        **options,
    )


def entry(path, size, symbol="f", metric="function_lines"):
    return f"[{metric}.{json.dumps(path)}]\n{json.dumps(symbol)} = {size}\n"


OLD_ENTRY = '[function_lines."src/old.py"]\n"f" = 70\n'
