import os
from collections.abc import Callable
from pathlib import Path

import pytest

from quality import cli, repository
from quality.repository import RepositoryFileError

NAME = "quality/config.toml"
VALID_CONFIG = '[gate]\nmandatory_jobs = ["checks"]\n'
NEEDS_PERMISSIONS = pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-0 path, so no permission is refused")
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


@pytest.fixture
def elsewhere(tmp_path: Path) -> Path:
    path = tmp_path / "elsewhere"
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
    [*UNUSABLE_CONFIGS, pytest.param(directory_without_permission, DENIED, id="quality/ mode 0", marks=NEEDS_PERMISSIONS)],
)
def test_an_unusable_file_is_one_repository_relative_error(arranged, repo, elsewhere, arrange, reason):
    """#52 rules 1, 3 and 5: an unusable name is an error that names it as given, never a resolved path."""
    with pytest.raises(RepositoryFileError) as raised:
        repository.read_text(arranged(arrange), NAME)
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
    with pytest.raises(RepositoryFileError) as raised:
        repository.is_on_disk(arranged(directory_without_permission), NAME)
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
    if command == "sonar findings":
        monkeypatch.chdir(repo)
        argv = ["sonar", "findings"]
    elif command == "hygiene --engines":
        engine_dir = scratch / "engines"
        engine_dir.mkdir(exist_ok=True)
        argv = ["hygiene", str(repo), "--engines", str(engine_dir)]
    else:
        argv = [*command.split(), str(repo)]
    code = cli.main(argv, transport=lambda url, token: requests.append(url))
    captured = capsys.readouterr()
    assert requests == [], "a config that cannot be judged is refused before any request"
    written = summary.read_text() if summary.exists() else ""
    return code, captured.out + captured.err + written


PREFIXES = {
    "hygiene --engines": "hygiene: cannot judge - ",
    "verdict": f"verdict: cannot judge - {NAME} at the head: ",
    "sonar findings": "sonar: cannot judge - ",
}


@pytest.mark.parametrize("command", list(PREFIXES))
@pytest.mark.parametrize(("arrange", "reason"), UNUSABLE_CONFIGS)
def test_an_unusable_config_is_exit_2_with_one_reason_in_every_command(
    git_repo, monkeypatch, capsys, command, arrange, reason
):
    """#52 rules 2-5, cells 1-9: every command that reads the config refuses it with the same reason after its own
    prefix, names no checkout path, and sonar sends no request; hygiene reads it before any engine runs."""
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
        ("hygiene", "hygiene: cannot judge - cannot read quality/baseline.toml: [Errno 13] Permission denied"),
        ("baseline write", "baseline: not written - cannot write quality/baseline.toml: [Errno 13] Permission denied"),
        ("verdict", f"verdict: cannot judge - {NAME} at the head: {DENIED}"),
        ("sonar findings", f"sonar: cannot judge - {DENIED}"),
    ],
    ids=["cell 12 hygiene", "cell 13 baseline write", "cell 4 verdict", "sonar findings"],
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
