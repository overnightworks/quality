import errno
import os
import stat
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from quality import cli, repository
from quality.repository import RepositoryFileError, TomlError, parse_toml
from tests.support.check import refusal_row
from tests.support.door import CHECK_WORD, door_in

NAME = "quality/config.toml"
VALID_CONFIG = '[gate]\nmandatory_jobs = ["checks"]\n'
NEEDS_PERMISSIONS = pytest.mark.skipif(
    os.geteuid() == 0, reason="root reads a mode-0 path, so no permission is refused"
)
Arrange = Callable[[Path, Path], None]


def config_at(repo: Path) -> Path:
    path = repo / NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def regular_file(repo: Path, elsewhere: Path) -> None:
    config_at(repo).write_text(VALID_CONFIG)


def link_inside_the_repository(repo: Path, elsewhere: Path) -> None:
    (repo / "real.toml").write_text(VALID_CONFIG)
    config_at(repo).symlink_to("../real.toml")


def link_out_of_the_repository(repo: Path, elsewhere: Path) -> None:
    (elsewhere / "outside.toml").write_text(VALID_CONFIG)
    config_at(repo).symlink_to(elsewhere / "outside.toml")


def dangling_link_out_of_the_repository(repo: Path, elsewhere: Path) -> None:
    config_at(repo).symlink_to(elsewhere / "missing.toml")


def dangling_link_inside(repo: Path, elsewhere: Path) -> None:
    config_at(repo).symlink_to("missing.toml")


def self_loop(repo: Path, elsewhere: Path) -> None:
    config_at(repo).symlink_to("config.toml")


def a_directory(repo: Path, elsewhere: Path) -> None:
    config_at(repo).mkdir()


def not_utf8(repo: Path, elsewhere: Path) -> None:
    config_at(repo).write_bytes(b"\xff\xfe")


def file_without_permission(repo: Path, elsewhere: Path) -> None:
    regular_file(repo, elsewhere)
    (repo / NAME).chmod(0)


def directory_without_permission(repo: Path, elsewhere: Path) -> None:
    regular_file(repo, elsewhere)
    (repo / "quality").chmod(0)


def nothing(repo: Path, elsewhere: Path) -> None:
    """The tree holds nothing at the name."""


def parent_is_a_file(repo: Path, elsewhere: Path) -> None:
    (repo / "quality").write_text("a file\n")


OUTSIDE = f"{NAME} resolves outside the repository"
DENIED = f"cannot read {NAME}: [Errno 13] Permission denied"
LOOP = f"cannot read {NAME}: [Errno 40] Too many levels of symbolic links"
DANGLING = f"cannot read {NAME}: [Errno 2] No such file or directory"
NOT_A_FILE = f"{NAME} is not a file"
NOT_UTF8 = f"cannot read {NAME}: 'utf-8' codec can't decode byte 0xff"

UNUSABLE_CONFIGS = [
    pytest.param(link_out_of_the_repository, OUTSIDE, id="cells 1-3 link out of the repository"),
    pytest.param(dangling_link_out_of_the_repository, OUTSIDE, id="link out of the repository to nothing"),
    pytest.param(file_without_permission, DENIED, id="cell 5 file mode 0", marks=NEEDS_PERMISSIONS),
    pytest.param(self_loop, LOOP, id="cell 6 self-loop"),
    pytest.param(dangling_link_inside, DANGLING, id="cell 7 dangling link inside"),
    pytest.param(a_directory, NOT_A_FILE, id="cell 8 a directory"),
    pytest.param(not_utf8, NOT_UTF8, id="cell 9 not UTF-8"),
]


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    return path


def restore_permissions(repo: Path) -> None:
    """Gives the arranged paths their permission back, so tmp_path can be removed."""
    for path in (repo / "quality", repo / NAME):
        if path.exists() and not path.is_symlink():
            path.chmod(0o755)


@pytest.fixture
def arranged(repo: Path, elsewhere: Path):
    def arrange(how: Arrange) -> Path:
        how(repo, elsewhere)
        return repo

    yield arrange
    restore_permissions(repo)


@pytest.mark.parametrize(
    "arrange", [regular_file, link_inside_the_repository], ids=["regular file", "link inside the repository"]
)
def test_a_file_inside_the_repository_is_read(arranged, arrange):
    """#52 rule 1: a regular file, or a link that stays inside the repository, is read."""
    assert repository.read_text(arranged(arrange), NAME) == VALID_CONFIG


@pytest.mark.parametrize("arrange", [nothing, parent_is_a_file], ids=["ENOENT", "ENOTDIR"])
def test_a_name_the_tree_does_not_hold_reads_as_absent(arranged, arrange):
    """#52 rules 1 and 3: only a name with nothing at it is absent."""
    assert repository.read_text(arranged(arrange), NAME) is None


@pytest.mark.parametrize(
    ("arrange", "reason"),
    [
        *UNUSABLE_CONFIGS,
        pytest.param(directory_without_permission, DENIED, id="quality/ mode 0", marks=NEEDS_PERMISSIONS),
    ],
)
def test_an_unusable_file_is_one_repository_relative_error(arranged, repo, elsewhere, arrange, reason):
    """#52 rules 1, 3 and 5: an unusable name is an error that names it as given, never a resolved path."""
    repo_with_the_file = arranged(arrange)
    with pytest.raises(RepositoryFileError) as raised:
        repository.read_text(repo_with_the_file, NAME)
    message = str(raised.value)
    assert (message.startswith(reason), str(repo) in message, str(elsewhere) in message) == (True, False, False)


def test_resolve_file_gives_the_file_inside_the_repository(arranged):
    """#52 rule 1: resolve_file is the one containment helper and yields the real path of a regular file."""
    repo = arranged(link_inside_the_repository)
    assert repository.resolve_file(repo, NAME) == Path(os.path.realpath(repo / "real.toml"))


@pytest.mark.parametrize(
    ("arrange", "on_disk"),
    [
        (regular_file, True),
        (nothing, False),
        (dangling_link_inside, False),
        (self_loop, False),
        (parent_is_a_file, False),
    ],
    ids=["file", "absent", "dangling link", "self-loop", "parent is a file"],
)
def test_a_name_that_leads_nowhere_is_not_on_disk(arranged, arrange, on_disk):
    """#52 rule 1: is_on_disk follows links; ENOENT, ENOTDIR and ELOOP mean gone."""
    assert repository.is_on_disk(arranged(arrange), NAME) is on_disk


@NEEDS_PERMISSIONS
def test_a_name_the_filesystem_refuses_to_reach_is_an_error(arranged, repo):
    """#52 rules 1 and 5: a name behind a directory without permission cannot be reached, named without a path."""
    locked_repo = arranged(directory_without_permission)
    with pytest.raises(RepositoryFileError) as raised:
        repository.is_on_disk(locked_repo, NAME)
    assert str(raised.value) == f"cannot reach {NAME}: [Errno 13] Permission denied"


def test_write_text_creates_the_file_and_its_directory(repo):
    """#52 rule 1: write_text writes the repository's own file."""
    repository.write_text(repo, NAME, VALID_CONFIG)
    assert (repo / NAME).read_text() == VALID_CONFIG


def test_a_write_through_a_symlink_is_refused_without_naming_its_target(repo, elsewhere):
    """#52 rule 5, cell 20: a write a link would redirect is refused, names no resolved path, and the target stays."""
    (elsewhere / "victim.toml").write_text("kept\n")
    config_at(repo).symlink_to(elsewhere / "victim.toml")
    with pytest.raises(RepositoryFileError) as raised:
        repository.write_text(repo, NAME, VALID_CONFIG)
    assert str(raised.value).startswith(f"cannot write {NAME}: ")
    assert (str(repo) in str(raised.value), str(elsewhere) in str(raised.value)) == (False, False)
    assert (elsewhere / "victim.toml").read_text() == "kept\n"


def read_only_file(repo: Path, elsewhere: Path) -> None:
    config_at(repo).write_text("old\n")
    config_at(repo).chmod(0o444)


def read_only_folder(repo: Path, elsewhere: Path) -> None:
    config_at(repo).write_text("old\n")
    config_at(repo).parent.chmod(0o555)


def raises(error: BaseException):
    def raiser(*args, **kwargs):
        raise error

    return raiser


def folder_listing(repo: Path) -> list[str]:
    return sorted(path.name for path in config_at(repo).parent.iterdir())


@pytest.fixture
def set_umask():
    original = os.umask(0)
    os.umask(original)
    yield os.umask
    os.umask(original)


@pytest.mark.parametrize(
    ("call", "error", "surfaces_as"),
    [
        ("fsync", OSError(errno.ENOSPC, "No space left on device"), RepositoryFileError),
        ("replace", OSError(errno.EACCES, "Permission denied"), RepositoryFileError),
        ("fsync", KeyboardInterrupt(), KeyboardInterrupt),
        ("replace", KeyboardInterrupt(), KeyboardInterrupt),
    ],
    ids=["sync fails", "replace fails", "interrupted at the sync", "interrupted before the replacement"],
)
def test_a_failed_write_keeps_the_old_file(repo, monkeypatch, call, error, surfaces_as):
    """A failure or an interrupt before the replacement leaves the old bytes and no sibling."""
    config_at(repo).write_text("old\n")
    monkeypatch.setattr(repository.os, call, raises(error))
    with pytest.raises(surfaces_as):
        repository.write_text(repo, NAME, "new\n")
    assert (config_at(repo).read_bytes(), folder_listing(repo)) == (b"old\n", ["config.toml"])


def test_an_interrupt_as_the_file_is_created_leaves_no_sibling(repo, monkeypatch):
    config_at(repo).write_text("old\n")
    real_open = os.open

    def create_then_interrupt(path, flags, *args, **kwargs):
        if flags & os.O_CREAT:
            os.close(real_open(path, flags, *args, **kwargs))
            raise KeyboardInterrupt
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(repository.os, "open", create_then_interrupt)
    with pytest.raises(KeyboardInterrupt):
        repository.write_text(repo, NAME, "new\n")
    assert (config_at(repo).read_bytes(), folder_listing(repo)) == (b"old\n", ["config.toml"])


def test_an_interrupt_after_the_replacement_leaves_the_new_file(repo, monkeypatch):
    config_at(repo).write_text("old\n")
    real_replace = os.replace

    def replace_then_interrupt(source, destination):
        real_replace(source, destination)
        raise KeyboardInterrupt

    monkeypatch.setattr(repository.os, "replace", replace_then_interrupt)
    with pytest.raises(KeyboardInterrupt):
        repository.write_text(repo, NAME, "new\n")
    assert (config_at(repo).read_bytes(), folder_listing(repo)) == (b"new\n", ["config.toml"])


def test_a_foreign_file_under_the_scratch_name_stays(repo, monkeypatch):
    config_at(repo).write_text("old\n")
    real_open = os.open
    foreign: list[Path] = []

    def occupy_the_scratch_name_then_open(path, flags, *args, **kwargs):
        if flags & os.O_CREAT:
            foreign.append(Path(path))
            Path(path).write_bytes(b"foreign\n")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(repository.os, "open", occupy_the_scratch_name_then_open)
    with pytest.raises(RepositoryFileError, match=r"\[Errno 17\]"):
        repository.write_text(repo, NAME, "new\n")
    assert (config_at(repo).read_bytes(), foreign[0].read_bytes()) == (b"old\n", b"foreign\n")


@pytest.mark.parametrize("mode", [0o600, 0o640, 0o664, 0o755])
def test_a_write_keeps_the_files_mode(repo, mode):
    config_at(repo).write_text("old\n")
    config_at(repo).chmod(mode)
    repository.write_text(repo, NAME, "new\n")
    assert stat.S_IMODE(config_at(repo).stat().st_mode) == mode


def test_a_private_file_stays_private_until_it_is_whole(repo, monkeypatch, set_umask):
    set_umask(0o022)
    config_at(repo).write_text("old\n")
    config_at(repo).chmod(0o644)
    real_fchmod = os.fchmod
    seen: list[tuple[int, int]] = []

    def record_then_apply(descriptor, mode):
        status = os.fstat(descriptor)
        seen.append((stat.S_IMODE(status.st_mode), status.st_size))
        real_fchmod(descriptor, mode)

    monkeypatch.setattr(repository.os, "fchmod", record_then_apply)
    repository.write_text(repo, NAME, "new text\n")
    assert seen == [(0o600, len("new text\n"))]


@pytest.mark.parametrize(
    ("umask", "mode"), [(0o022, 0o644), (0o077, 0o600), (0o002, 0o664)], ids=["0o022", "0o077", "0o002"]
)
def test_a_new_file_takes_the_umasks_mode(repo, set_umask, umask, mode):
    set_umask(umask)
    repository.write_text(repo, NAME, "new\n")
    assert stat.S_IMODE(config_at(repo).stat().st_mode) == mode


@NEEDS_PERMISSIONS
@pytest.mark.parametrize("how", [read_only_file, read_only_folder], ids=["file", "folder"])
def test_a_read_only_target_refuses_the_write(arranged, how):
    repo = arranged(how)
    with pytest.raises(RepositoryFileError, match=r"\[Errno 13\]"):
        repository.write_text(repo, NAME, "new\n")
    assert (config_at(repo).read_bytes(), folder_listing(repo)) == (b"old\n", ["config.toml"])


def test_a_write_syncs_the_whole_content_before_it_replaces(repo, monkeypatch):
    """The order of the sync and the replacement is the contract, and the sync covers the finished content."""
    text = "new text\n"
    events: list[tuple[str, int | None]] = []
    real_fsync, real_replace = os.fsync, os.replace

    def record_size_then_sync(descriptor):
        events.append(("fsync", os.fstat(descriptor).st_size))
        real_fsync(descriptor)

    def record_then_replace(source, destination):
        events.append(("replace", None))
        real_replace(source, destination)

    monkeypatch.setattr(repository.os, "fsync", record_size_then_sync)
    monkeypatch.setattr(repository.os, "replace", record_then_replace)
    repository.write_text(repo, NAME, text)
    assert events == [("fsync", len(text)), ("replace", None)]


@pytest.mark.integration
@pytest.mark.parametrize(
    ("moment", "bytes_left", "siblings_left"),
    [("before", b"old\n", 1), ("after", b"new\n", 0)],
    ids=["before", "after"],
)
def test_a_killed_write_leaves_one_whole_file(repo, moment, bytes_left, siblings_left):
    config_at(repo).write_text("old\n")
    child = """
import os, signal, sys
from pathlib import Path
from quality import repository

root, name, moment = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
real_replace = os.replace

def replace_and_kill(source, destination):
    if moment == "after":
        real_replace(source, destination)
    os.kill(os.getpid(), signal.SIGKILL)

os.replace = replace_and_kill
repository.write_text(root, name, "new\\n")
"""
    finished = subprocess.run([sys.executable, "-I", "-c", child, str(repo), NAME, moment], timeout=60, check=False)
    siblings = [path for path in config_at(repo).parent.iterdir() if path.name != "config.toml"]
    assert finished.returncode == -9
    assert (config_at(repo).read_bytes(), len(siblings)) == (bytes_left, siblings_left)
    assert [(path.name.startswith("."), path.read_bytes()) for path in siblings] == [(True, b"new\n")] * siblings_left


def checkout_paths(repo: Path) -> tuple[str, ...]:
    return str(repo), os.path.realpath(repo)


@pytest.fixture
def git_repo(build_repo, tmp_path_factory, monkeypatch):
    """A committed repository (build_repo builds in tmp_path) and a scratch place outside it, environment clean."""
    for name in ("GATE_BASE_SHA", "GITHUB_STEP_SUMMARY"):
        monkeypatch.delenv(name, raising=False)
    repo = build_repo({"README.md": "x\n"})
    scratch = tmp_path_factory.mktemp("outside")
    yield repo, scratch
    restore_permissions(repo)


def run_command(command: str, repo: Path, scratch: Path, monkeypatch, capsys) -> tuple[int, str]:
    """Drives one door command through cli.main and returns its exit code and everything it printed."""
    summary = scratch / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    requests: list[str] = []
    code = door_in(repo, *command.split(), transport=lambda url, token: requests.append(url))
    captured = capsys.readouterr()
    assert requests == [], "a config that cannot be judged is refused before any request"
    written = summary.read_text() if summary.exists() else ""
    return code, captured.out + captured.err + written


PREFIXES = {
    CHECK_WORD: f"{refusal_row('secrets')}",
    "check debt --fix": f"{refusal_row('debt')}",
    "verdict": f"verdict: cannot judge - {NAME} at the head: ",
    "sonar findings": "sonar: cannot judge - ",
}


@pytest.mark.parametrize("command", list(PREFIXES))
@pytest.mark.parametrize(("arrange", "reason"), UNUSABLE_CONFIGS)
def test_an_unusable_config_is_exit_2_with_one_reason_in_every_command(
    git_repo, monkeypatch, capsys, command, arrange, reason
):
    """#52 rules 2-5, cells 1-9: every command that reads the config refuses it with the same reason after its own
    prefix, names no checkout path, and sonar sends no request; the check reads it before any scanner runs."""
    repo, scratch = git_repo
    arrange(repo, scratch)
    code, output = run_command(command, repo, scratch, monkeypatch, capsys)
    assert code == 2, output
    assert PREFIXES[command] + reason in output
    assert not any(path in output for path in checkout_paths(repo)), output
    assert "Traceback" not in output


@NEEDS_PERMISSIONS
@pytest.mark.parametrize(
    ("command", "refusal"),
    [
        (CHECK_WORD, f"{refusal_row('debt')}cannot read quality/baseline.toml: [Errno 13] Permission denied"),
        ("check debt --fix", f"{refusal_row('debt')}cannot read quality/baseline.toml: [Errno 13] Permission denied"),
        ("verdict", f"verdict: cannot judge - {NAME} at the head: {DENIED}"),
        ("sonar findings", f"sonar: cannot judge - {DENIED}"),
    ],
    ids=["cell 12 check", "cell 13 check debt --fix", "cell 4 verdict", "sonar findings"],
)
def test_a_quality_directory_without_search_permission_is_exit_2_in_every_command(
    git_repo, monkeypatch, capsys, command, refusal
):
    """#52 rules 2 and 5, cells 4, 12 and 13: a quality/ directory git cannot enter is exit 2 with the reason, no
    traceback and no checkout path, never a baseline taken for absent."""
    repo, scratch = git_repo
    directory_without_permission(repo, scratch)
    code, output = run_command(command, repo, scratch, monkeypatch, capsys)
    assert (code, refusal in output, "Traceback" in output) == (2, True, False), output
    assert not any(path in output for path in checkout_paths(repo)), output


def assume_a_foreign_owner(repo: Path, monkeypatch) -> None:
    monkeypatch.setenv("GIT_TEST_ASSUME_DIFFERENT_OWNER", "1")


def point_git_dir_at_a_file(repo: Path, monkeypatch) -> None:
    monkeypatch.setenv("GIT_DIR", str(repo / "README.md"))


@pytest.mark.parametrize(
    "git_refusal",
    [assume_a_foreign_owner, point_git_dir_at_a_file],
    ids=["dubious ownership", "invalid gitfile"],
)
@pytest.mark.parametrize(
    ("argv", "prefix"),
    [
        ([CHECK_WORD], "error: "),
        (["check", "debt", "--fix"], "error: "),
        (["sonar", "properties"], "sonar: cannot judge - "),
    ],
    ids=["check", "check debt --fix", "sonar properties"],
)
def test_a_git_refusal_names_the_path_as_given_and_never_the_checkout(
    git_repo, monkeypatch, capsys, git_refusal, argv, prefix
):
    """#52 rule 5: when git refuses to list the files, every command that asks it says so with the PATH as given;
    git's own words, which carry the resolved checkout path, are not printed."""
    repo, _ = git_repo
    monkeypatch.setenv("SONAR_TOKEN", "a-token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "overnightworks/quality")
    monkeypatch.chdir(repo)
    git_refusal(repo, monkeypatch)
    code = cli.main(argv)
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert (code, f"{prefix}. is not a readable git repository" in output, "Traceback" in output) == (2, True, False), (
        output
    )
    assert not any(path in output for path in checkout_paths(repo)), output


def test_valid_toml_is_parsed_into_its_document():
    assert parse_toml('a = 1\n[b]\nc = "d"\n') == {"a": 1, "b": {"c": "d"}}


@pytest.mark.parametrize(
    "text",
    ["[table\n", f"a = {'9' * 5000}\n", f"a = {'[' * 5000}{']' * 5000}\n"],
    ids=["syntax error", "oversized integer", "nested too deep"],
)
def test_toml_the_reader_refuses_is_one_error(text):
    with pytest.raises(TomlError):
        parse_toml(text)
