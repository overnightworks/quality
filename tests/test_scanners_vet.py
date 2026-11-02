"""A scanner's stderr reaches a refusal's fact only once gitleaks has cleared all of it."""

import shlex
import tempfile
from pathlib import Path

import pytest

from quality import scanners
from tests.support.advisories import reporting, uv_lock
from tests.support.check import area_fact
from tests.support.door import check_door
from tests.support.python_lint import fake_ruff
from tests.support.scanners import OUTPUTS, fake_gitleaks, fake_scanner, fake_zizmor, flagging, lacking, vetting
from tests.support.secrets import FAKE_KEY, multi_line_key

WORKFLOW = "on: push\npermissions: {}\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo hi\n"


def shouting(lines: list[str]) -> str:
    """Shell that writes `lines` to stderr."""
    return f"printf '%s\\n' {shlex.join(lines)} >&2"


def refused(repo: Path, capsys, **keywords) -> tuple[int, str, str]:
    """The exit, stdout and stderr of a `check` of `repo`."""
    code = check_door(repo, **keywords)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def judged_repository(build_repo) -> Path:
    return build_repo({".github/workflows/w.yml": WORKFLOW, "uv.lock": uv_lock(), "src/a.py": "x = 1\n"})


@pytest.mark.parametrize(
    ("who", "area", "fact"),
    [
        ("actionlint", "workflows", "actionlint exited 3, neither 'clean' nor 'findings'"),
        ("zizmor", "workflows", "zizmor exited 3, neither 'clean' nor 'findings'"),
        ("osv-scanner", "dependencies", "osv-scanner exited 3, neither 'clean' nor 'findings'"),
        ("ruff", "code", "ruff exited 3"),
        ("ruff's lint failure", "code", "ruff could not lint every file"),
        ("zizmor's version", "workflows", "zizmor version exited 3"),
    ],
    ids=["actionlint", "zizmor", "osv-scanner", "ruff", "ruff's lint failure", "zizmor's version"],
)
def test_a_refused_scanners_value_is_withheld(build_repo, served_scanners, elsewhere, tmp_path, monkeypatch, capsys, who, area, fact):
    fake_gitleaks(served_scanners, on_scan=vetting(flagging(FAKE_KEY)))
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    said = shouting([f"aws_access_key_id = {FAKE_KEY}"])
    refusing = f"{said}; exit 3"
    clean_zizmor = fake_zizmor(elsewhere / "clean", "echo '[]'")
    keywords = {"locate_zizmor": clean_zizmor}
    if who == "actionlint":
        fake_scanner(served_scanners, scanners.ACTIONLINT, refusing)
    elif who == "zizmor":
        keywords["locate_zizmor"] = fake_zizmor(elsewhere / "refusing", refusing)
    elif who == "osv-scanner":
        fake_scanner(served_scanners, scanners.OSV_SCANNER, refusing)
    elif who == "ruff":
        keywords["locate_ruff"] = lambda: str(fake_ruff(elsewhere, refusing))
    elif who == "ruff's lint failure":
        failing = shouting([f"warning: Failed to lint src/a.py: aws_access_key_id = {FAKE_KEY}"])
        keywords["locate_ruff"] = lambda: str(fake_ruff(elsewhere, f"{failing}; echo '[]'"))
    else:
        failing_version = elsewhere / "failing" / "zizmor"
        failing_version.parent.mkdir()
        failing_version.write_text(f"#!/bin/sh\n{refusing}\n")
        failing_version.chmod(0o755)
        keywords["locate_zizmor"] = lambda: str(failing_version)
    code, out, err = refused(judged_repository(build_repo), capsys, **keywords)
    assert code == 2
    assert area_fact(out, area) == f"{fact} · 1 stderr line withheld · not cleared by gitleaks"
    assert FAKE_KEY not in out
    assert FAKE_KEY not in err
    assert not summary.exists()
    assert FAKE_KEY in (served_scanners / "gitleaks.vetted").read_text()


def test_a_gitleaks_version_refusal_quotes_nothing(build_repo, served_scanners, capsys):
    fake_gitleaks(served_scanners, on_scan=vetting(flagging(FAKE_KEY)), on_version=f"{shouting([FAKE_KEY])}; exit 3")
    code, out, err = refused(build_repo({"docs/a.md": "x\n"}), capsys)
    assert code == 2
    assert area_fact(out, "secrets") == "gitleaks version exited 3"
    assert FAKE_KEY not in out + err
    assert not (served_scanners / "gitleaks.vetted").exists()


def test_the_whole_stderr_is_vetted_before_anything_is_cut(build_repo, served_scanners, elsewhere, capsys):
    fake_gitleaks(served_scanners, on_scan=vetting(flagging(FAKE_KEY)))
    lines = [f"aws_access_key_id = {FAKE_KEY}", *(f"line {number}" for number in range(2, 9))]
    ruff = fake_ruff(elsewhere, f"{shouting(lines)}; exit 2")
    code, out, err = refused(build_repo({"src/a.py": "x = 1\n"}), capsys, locate_ruff=lambda: str(ruff))
    assert code == 2
    assert area_fact(out, "code") == "ruff exited 2 · 8 stderr lines withheld · not cleared by gitleaks"
    assert [line for line in lines if line in out + err] == []
    assert (served_scanners / "gitleaks.vetted").read_text().endswith("\n".join(lines) + "\n")


def test_a_cleared_stderr_stands_as_a_tail(build_repo, served_scanners, elsewhere, capsys):
    fake_gitleaks(served_scanners, on_scan=vetting(flagging(FAKE_KEY)))
    ruff = fake_ruff(elsewhere, f"{shouting(['first', 'second'])}; exit 2")
    code, out, _ = refused(build_repo({"src/a.py": "x = 1\n"}), capsys, locate_ruff=lambda: str(ruff))
    assert code == 2
    assert area_fact(out, "code") == r"ruff exited 2: first\x0asecond"
    assert (served_scanners / "gitleaks.vetted").exists()


@pytest.mark.parametrize(
    ("on_vet", "timeout"),
    [
        ("exit 1", 5),
        ("exit 3", 5),
        ("exit 0", 5),
        ('echo x > "$report"; exit 0', 5),
        (f'cp {OUTPUTS / "gitleaks-finding.json"} "$report"; exit 0', 5),
        ('echo "[]" > "$report"; exit 7', 5),
        ("exec sleep 30", 0.2),
        (None, 5),
    ],
    ids=[
        "its own error", "another exit", "no report", "a report that is no JSON", "a clean exit beside a finding",
        "a leak exit beside nothing", "a stall", "gitleaks missing",
    ],
)
def test_anything_but_a_clean_answer_withholds_the_text(
    build_repo, served_scanners, elsewhere, use_mise, monkeypatch, capsys, on_vet, timeout
):
    fake_gitleaks(served_scanners, on_scan=vetting(on_vet or "exit 0"))
    monkeypatch.setattr(scanners, "VET_TIMEOUT_SECONDS", timeout)
    if on_vet is None:
        use_mise(lacking(served_scanners, scanners.GITLEAKS))
    ruff = fake_ruff(elsewhere, f"{shouting(['boom'])}; exit 2")
    code, out, err = refused(build_repo({"src/a.py": "x = 1\n"}), capsys, locate_ruff=lambda: str(ruff))
    assert code == 2
    assert area_fact(out, "code") == "ruff exited 2 · 1 stderr line withheld · not cleared by gitleaks"
    assert "boom" not in out + err


@pytest.mark.integration
def test_no_scratch_directory_withholds_the_text(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "missing"))
    assert scanners.vetted_tail("boom\n") == " · 1 stderr line withheld · not cleared by gitleaks"


def test_a_clean_run_starts_no_vet(build_repo, served_scanners, elsewhere, capsys):
    fake_gitleaks(served_scanners, on_scan=vetting(flagging(FAKE_KEY)))
    noted = shouting(["noted"])
    fake_scanner(served_scanners, scanners.ACTIONLINT, f"{noted}; echo '[]'")
    fake_scanner(served_scanners, scanners.OSV_SCANNER, reporting(OUTPUTS / "osv-scanner-clean.json", 0, "noted"))
    ruff = fake_ruff(elsewhere, f"{noted}; echo '[]'")
    zizmor = fake_zizmor(elsewhere / "zizmor", f"{noted}; echo '[]'")
    code, _, _ = refused(judged_repository(build_repo), capsys, locate_ruff=lambda: str(ruff), locate_zizmor=zizmor)
    assert code == 0
    assert not (served_scanners / "gitleaks.vetted").exists()


def test_a_refusal_without_stderr_starts_no_vet(build_repo, served_scanners, elsewhere, capsys):
    fake_gitleaks(served_scanners, on_scan=vetting(flagging(FAKE_KEY)))
    ruff = fake_ruff(elsewhere, "exit 2")
    code, out, _ = refused(build_repo({"src/a.py": "x = 1\n"}), capsys, locate_ruff=lambda: str(ruff))
    assert code == 2
    assert area_fact(out, "code") == "ruff exited 2"
    assert not (served_scanners / "gitleaks.vetted").exists()


def test_the_vet_judges_as_the_secret_scan_judges(build_repo, served_scanners, elsewhere, tmp_path, monkeypatch, capsys):
    fake_gitleaks(served_scanners, on_scan=vetting("exit 1"))
    scratch_root = tmp_path / "scratch-root"
    scratch_root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch_root))
    ruff = fake_ruff(elsewhere, f"{shouting(['boom'])}; exit 2")
    refused(build_repo({"src/a.py": "x = 1\n"}), capsys, locate_ruff=lambda: str(ruff))
    given = (served_scanners / "gitleaks.arguments").read_text().splitlines()

    def after(flag: str) -> str:
        return given[given.index(flag) + 1]

    assert given[0] == "stdin"
    assert "--redact" in given
    assert "--ignore-gitleaks-allow" in given
    assert after("--config") == str(scanners.GITLEAKS_CONFIG)
    assert Path(after("--gitleaks-ignore-path")) == Path(after("--report-path")).parent
    assert list(scratch_root.iterdir()) == []


@pytest.mark.scanner
@pytest.mark.parametrize(
    ("text", "withheld", "ignored"),
    [
        ("boom\n", False, False),
        (f"error: aws_access_key_id = {FAKE_KEY}\nsecond\n", True, False),
        (f"error:\t{FAKE_KEY}\nsecond\n", True, False),
        (multi_line_key() + "three\nmore\nlines\n", True, False),
        (f"{FAKE_KEY}\n" + "tidy\n" * 6, True, False),
        (f"aws_access_key_id = {FAKE_KEY}  # gitleaks:allow\nsecond\n", True, False),
        (f"%PDF-1.4\naws_access_key_id = {FAKE_KEY}\n", True, False),
        (f"aws_access_key_id = {FAKE_KEY}\nsecond\n", True, True),
    ],
    ids=[
        "clean text", "a key", "a key after a tab", "a multi-line key", "a key above the tail", "a key beside gitleaks:allow",
        "a binary type's first bytes", "a .gitleaksignore in the repository",
    ],
)
def test_the_real_gitleaks_vets_a_refused_scanners_stderr(build_repo, installed_scanners, elsewhere, capsys, text, withheld, ignored):
    repo = build_repo({"src/a.py": "x = 1\n"})
    if ignored:
        (repo / ".gitleaksignore").write_text("".join(f":aws-access-token:{number}\n" for number in (1, 2, 3)))
    scenario = elsewhere / "stderr.txt"
    scenario.write_text(text)
    ruff = fake_ruff(elsewhere, f"cat {shlex.quote(str(scenario))} >&2; exit 2")
    code, out, err = refused(repo, capsys, locate_ruff=lambda: str(ruff))
    assert code == 2
    lines = len(text.splitlines())
    expected = f"ruff exited 2 · {lines} stderr lines withheld · not cleared by gitleaks" if withheld else "ruff exited 2: boom"
    assert area_fact(out, "code") == expected
    assert FAKE_KEY not in out + err
    assert [line for line in text.splitlines() if withheld and len(line) > 15 and line in out + err] == []
