import subprocess
from pathlib import Path

from quality.ratchet import BASELINE_PATH

GIT_IDENTITY = ["-c", "user.name=t", "-c", "user.email=t@example.invalid"]


def make_repo(directory: Path, files: dict[str, str]) -> Path:
    """A git repository at `directory` whose one commit tracks exactly `files`, ignored names included."""
    git = ["git", "-C", str(directory), *GIT_IDENTITY]
    subprocess.run([*git, "init", "-q"], check=True)
    for name, content in files.items():
        target = directory / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    subprocess.run([*git, "add", "-A", "-f"], check=True)
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "fixture"], check=True)
    return directory


def head_commit(repo: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()


def put(path: Path, content: str | bytes | None) -> None:
    """Writes `content` to `path`, or removes the file when `content` is None."""
    if content is None:
        path.unlink(missing_ok=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode())


def head_of(url: str) -> str:
    directory = url.removeprefix("file://")
    return subprocess.run(
        ["git", "-C", directory, "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()


def git_pull(directory, base_baseline, merge_baseline, moves=()):
    """A bare repository holding a base commit and a merge commit that moves each (old, new) file and holds the
    given baselines; returns its path and the two shas."""
    work = directory / "work"
    git = ["git", "-C", str(work), *GIT_IDENTITY]
    subprocess.run(["git", "init", "-q", str(work)], check=True)
    for old, _ in moves:
        name = old.encode("ascii", "backslashreplace").decode()
        put(work / old, "".join(f"{name} row {number} that no other file shares\n" for number in range(30)))
    put(work / BASELINE_PATH, base_baseline)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "base"], check=True)
    base_sha = subprocess.run([*git, "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    for old, new in moves:
        (work / new).parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([*git, "mv", old, new], check=True)
    put(work / BASELINE_PATH, merge_baseline)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "merge"], check=True)
    merge_sha = subprocess.run([*git, "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    remote = directory / "remote.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(work), str(remote)], check=True)
    return str(remote), base_sha, merge_sha
