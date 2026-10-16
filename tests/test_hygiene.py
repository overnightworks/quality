import re
import subprocess
from pathlib import Path

import pytest

from fleet_quality import cli

EDGE_STACK = (Path(__file__).parent / "data" / "edge_stack.yml").read_text(encoding="utf-8")


def compose(*port_lines: str) -> dict[str, str]:
    body = "".join(f"      {line}\n" for line in port_lines)
    return {"compose.yaml": f"services:\n  web:\n    image: x\n    ports:\n{body}"}


def run(capsys: pytest.CaptureFixture[str], path: Path) -> tuple[int, str]:
    code = cli.main(["hygiene", str(path)])
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def unreadable_compose_repo(build_repo, tmp_path):
    repo = build_repo(compose('- "127.0.0.1:8080:80"'))
    (repo / "compose.yaml").unlink()
    return repo


def truncated_compose_repo(build_repo, tmp_path):
    return build_repo({"compose.yaml": "services: [\n"})


def non_utf8_filename_repo(build_repo, tmp_path):
    return build_repo({"\udcff.txt": "x\n"})


@pytest.mark.parametrize(
    ("arrange", "expected_code", "expected_text"),
    [
        (lambda build_repo, tmp_path: build_repo({}), 0, "hygiene: clean (2 rules)"),
        (non_utf8_filename_repo, 0, "hygiene: clean (2 rules)"),
        (lambda build_repo, tmp_path: tmp_path, 2, "cannot judge"),
        (lambda build_repo, tmp_path: build_repo({".env": "A=1\n"}), 1, "finding(s)"),
        (unreadable_compose_repo, 2, "cannot judge"),
        (truncated_compose_repo, 2, "cannot judge"),
    ],
    ids=["empty-repository", "non-utf8-filename", "not-a-git-repository", "tracked-env-file",
         "unreadable-compose", "malformed-compose-yaml"],
)
def test_exit_code_tells_clean_from_cannot_judge(build_repo, tmp_path, capsys, arrange, expected_code, expected_text):
    """#3 rule 1: exit 0 clean, 1 findings, 2 cannot judge."""
    code, output = run(capsys, arrange(build_repo, tmp_path))
    assert code == expected_code
    assert expected_text in output


def test_compose_specific_tags_are_judged_not_refused(build_repo, capsys):
    """#3 rule 2: valid Compose YAML tags are read, and their ports still checked."""
    body = "services:\n  web:\n    image: x\n    ports: !override\n      - \"127.0.0.1:80:80\"\n    profiles: !reset []\n"
    code, output = run(capsys, build_repo({"compose.yaml": body}))
    assert (code, "hygiene: clean (2 rules)" in output) == (0, True)


def assert_refusal(output: str, rule: str, path: str) -> None:
    assert re.search(rf"^{rule} {re.escape(path)} - \S.*\n  Fix: \S", output, re.MULTILINE), output


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


@pytest.mark.parametrize(
    ("files", "refused_path"),
    [
        (compose('- "8080:80"'), "compose.yaml"),
        (compose('- "80"'), "compose.yaml"),
        (compose("- 80"), "compose.yaml"),
        (compose('- "0.0.0.0:8080:80"'), "compose.yaml"),
        (compose("- target: 80", "  published: 8080"), "compose.yaml"),
        (compose("- target: 80", "  host_ip: 0.0.0.0"), "compose.yaml"),
        ({"compose.yaml": "services:\n  a:\n    ports: 8080\n"}, "compose.yaml"),
        ({"deploy/docker-compose.prod.yml": "services:\n  a:\n    ports: ['80:80']\n"}, "deploy/docker-compose.prod.yml"),
        (compose('- "127.0.0.1:8080:80"'), None),
        (compose("- target: 80", "  host_ip: 127.0.0.1"), None),
        ({"compose.yaml": EDGE_STACK}, None),
        ({"README.md": "no compose here\n"}, None),
    ],
)
def test_compose_ports_must_bind_loopback(build_repo, capsys, files, refused_path):
    """#3 rule 2/4: a compose port beyond loopback is refused with rule id, path, reason and fix; edge's real file passes."""
    code, output = run(capsys, build_repo(files))
    assert code == (0 if refused_path is None else 1)
    if refused_path:
        assert_refusal(output, "compose-port", refused_path)


def test_quality_tracks_no_env_or_compose_file():
    """#3 rule 5: the quality repository passes its own hygiene check and tracks no env or compose file."""
    root = Path(__file__).resolve().parents[1]
    tracked = subprocess.run(["git", "-C", str(root), "ls-files"], capture_output=True, text=True, check=True).stdout
    assert cli.main(["hygiene", str(root)]) == 0
    assert "compose" not in tracked and ".env" not in tracked
