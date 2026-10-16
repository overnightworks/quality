import subprocess
from pathlib import Path

import pytest

from fleet_quality import cli

EDGE_COMPOSE = 'services:\n  web:\n    image: x\n    ports:\n      - "127.0.0.1:8080:80"\n    expose:\n      - "9000"\n'


def compose(*port_lines: str) -> dict[str, str]:
    body = "".join(f"      {line}\n" for line in port_lines)
    return {"compose.yaml": f"services:\n  web:\n    image: x\n    ports:\n{body}"}


def run(capsys: pytest.CaptureFixture[str], path: Path) -> tuple[int, str]:
    code = cli.main(["hygiene", str(path)])
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def test_empty_repository_is_clean(build_repo, capsys):
    assert run(capsys, build_repo({})) == (0, "hygiene: clean (2 rules)\n")


def test_directory_that_is_not_a_git_repository_cannot_be_judged(tmp_path, capsys):
    code, output = run(capsys, tmp_path)
    assert code == 2
    assert "cannot judge" in output


def test_unreadable_compose_file_cannot_be_judged(build_repo, capsys):
    repo = build_repo({"compose.yaml": EDGE_COMPOSE})
    (repo / "compose.yaml").unlink()
    assert run(capsys, repo)[0] == 2


@pytest.mark.parametrize(
    ("name", "refused"),
    [
        (".env", True),
        ("config/prod.env", True),
        (".env.local", True),
        (".env.example", False),
        ("docker/.env.docker.example", False),
        ("environment.txt", False),
    ],
)
def test_tracked_env_files(build_repo, capsys, name, refused):
    code, output = run(capsys, build_repo({name: "A=1\n"}))
    assert code == (1 if refused else 0)
    if refused:
        assert "env-file" in output and name in output and "Fix:" in output


def test_untracked_ignored_env_file_is_not_a_finding(build_repo, capsys):
    repo = build_repo({".gitignore": ".env\n"})
    (repo / ".env").write_text("A=1\n")
    assert run(capsys, repo)[0] == 0


@pytest.mark.parametrize(
    ("files", "refused"),
    [
        (compose('- "8080:80"'), True),
        (compose('- "80"'), True),
        (compose("- 80"), True),
        (compose('- "0.0.0.0:8080:80"'), True),
        (compose("- target: 80", "  published: 8080"), True),
        (compose("- target: 80", "  host_ip: 0.0.0.0"), True),
        ({"compose.yaml": "services: [unclosed"}, True),
        ({"deploy/docker-compose.prod.yml": "services:\n  a:\n    ports: ['80:80']\n"}, True),
        (compose('- "127.0.0.1:8080:80"'), False),
        (compose("- target: 80", "  host_ip: 127.0.0.1"), False),
        ({"compose.yaml": EDGE_COMPOSE}, False),
        ({"README.md": "no compose here\n"}, False),
    ],
)
def test_compose_ports_must_bind_loopback(build_repo, capsys, files, refused):
    code, output = run(capsys, build_repo(files))
    assert code == (1 if refused else 0)
    if refused:
        assert "compose-port" in output and "Fix:" in output


def test_quality_tracks_no_env_or_compose_file():
    root = Path(__file__).resolve().parents[1]
    tracked = subprocess.run(["git", "-C", str(root), "ls-files"], capture_output=True, text=True, check=True).stdout
    assert cli.main(["hygiene", str(root)]) == 0
    assert "compose" not in tracked and ".env" not in tracked
