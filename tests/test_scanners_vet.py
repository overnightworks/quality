"""A scanner's stderr reaches a refusal's fact only once gitleaks has cleared all of it."""

import shlex
import tempfile
from pathlib import Path

import pytest

from quality import scanners
from tests.support.advisories import uv_lock
from tests.support.check import area_fact
from tests.support.door import check_door
from tests.support.python_lint import fake_ruff
from tests.support.scanners import (
    OUTPUTS,
    fake_gitleaks,
    fake_scanner,
    fake_zizmor,
    flagging,
    lacking,
    noting_on_every_call,
    vetting,
)
from tests.support.secrets import FAKE_KEY, multi_line_key


def shouting(lines: list[str]) -> str:
    """Shell that writes `lines` to stderr."""
    return f"printf '%s\\n' {shlex.join(lines)} >&2"


def refused(repo: Path, capsys, **keywords) -> tuple[int, str, str]:
    """The exit, stdout and stderr of a `check` of `repo`."""
    code = check_door(repo, **keywords)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def judged_repository(build_repo) -> Path:
    return build_repo({".github/workflows/w.yml": "on: push\npermissions: {}\njobs:\n  a:\n    runs-on: ubuntu-latest\n    timeout-minutes: 15\n    steps:\n      - run: echo hi\n", "uv.lock": uv_lock(), "src/a.py": "x = 1\n"})


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
def test_a_refused_scanners_value_is_withheld(build_repo, served_scanners, elsewhere, summary, capsys, who, area, fact):
    """VERBOSE-14, VERBOSE-15, VERBOSE-17, VERBOSE-18: a value in a refused scanner's stderr goes through gitleaks, is
    withheld and counted in the fact."""
    fake_gitleaks(served_scanners, on_scan=vetting(flagging(FAKE_KEY)))
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
    """VERBOSE-23: a gitleaks version refusal quotes nothing and starts no vet."""
    fake_gitleaks(served_scanners, on_scan=vetting(flagging(FAKE_KEY)), on_version=f"{shouting([FAKE_KEY])}; exit 3")
    code, out, err = refused(build_repo({"docs/a.md": "x\n"}), capsys)
    assert code == 2
    assert area_fact(out, "secrets") == "gitleaks version exited 3"
    assert FAKE_KEY not in out + err
    assert not (served_scanners / "gitleaks.vetted").exists()


def test_the_whole_stderr_is_vetted_before_anything_is_cut(build_repo, served_scanners, elsewhere, capsys):
    """VERBOSE-15, VERBOSE-17, VERBOSE-18: the whole stderr is vetted before a tail is cut, and every line is withheld
    and counted."""
    fake_gitleaks(served_scanners, on_scan=vetting(flagging(FAKE_KEY)))
    lines = [f"aws_access_key_id = {FAKE_KEY}", *(f"line {number}" for number in range(2, 9))]
    ruff = fake_ruff(elsewhere, f"{shouting(lines)}; exit 2")
    code, out, err = refused(build_repo({"src/a.py": "x = 1\n"}), capsys, locate_ruff=lambda: str(ruff))
    assert code == 2
    assert area_fact(out, "code") == "ruff exited 2 · 8 stderr lines withheld · not cleared by gitleaks"
    assert [line for line in lines if line in out + err] == []
    assert (served_scanners / "gitleaks.vetted").read_text().endswith("\n".join(lines) + "\n")


def test_a_cleared_stderr_stands_as_a_tail(build_repo, served_scanners, elsewhere, capsys):
    """VERBOSE-16, VERBOSE-21, MASK-10: a stderr cleared by exit 0 and an empty report stands as one fact line."""
    fake_gitleaks(served_scanners, on_scan=vetting(flagging(FAKE_KEY)))
    ruff = fake_ruff(elsewhere, f"{shouting(['first', 'second'])}; exit 2")
    code, out, _ = refused(build_repo({"src/a.py": "x = 1\n"}), capsys, locate_ruff=lambda: str(ruff))
    assert code == 2
    assert area_fact(out, "code") == r"ruff exited 2: first\x0asecond"
    assert (served_scanners / "gitleaks.vetted").exists()


@pytest.mark.parametrize(
    "on_vet",
    [
        "exit 1",
        "exit 3",
        "exit 0",
        'echo x > "$report"; exit 0',
        f'cp {OUTPUTS / "gitleaks-finding.json"} "$report"; exit 0',
        'echo "[]" > "$report"; exit 7',
        None,
    ],
    ids=[
        "its own error", "another exit", "no report", "a report that is no JSON", "a clean exit beside a finding",
        "a leak exit beside nothing", "gitleaks missing",
    ],
)
def test_anything_but_a_clean_answer_withholds_the_text(build_repo, served_scanners, elsewhere, use_mise, capsys, on_vet):
    """VERBOSE-16, VERBOSE-17: any answer but exit 0 with an empty report withholds the text, a missing or unreadable
    one too."""
    fake_gitleaks(served_scanners, on_scan=vetting(on_vet or "exit 0"))
    if on_vet is None:
        use_mise(lacking(served_scanners, scanners.GITLEAKS))
    ruff = fake_ruff(elsewhere, f"{shouting(['boom'])}; exit 2")
    code, out, err = refused(build_repo({"src/a.py": "x = 1\n"}), capsys, locate_ruff=lambda: str(ruff))
    assert code == 2
    assert area_fact(out, "code") == "ruff exited 2 · 1 stderr line withheld · not cleared by gitleaks"
    assert "boom" not in out + err


def test_a_stalled_vet_withholds_the_text(build_repo, served_scanners, elsewhere, monkeypatch, capsys):
    """VERBOSE-17: a vet that outlasts its time limit withholds the text."""
    fake_gitleaks(served_scanners, on_scan=vetting('echo "[]" > "$report"; exec sleep 30'))
    monkeypatch.setattr(scanners, "VET_TIMEOUT_SECONDS", 0.2)
    ruff = fake_ruff(elsewhere, f"{shouting(['boom'])}; exit 2")
    code, out, err = refused(build_repo({"src/a.py": "x = 1\n"}), capsys, locate_ruff=lambda: str(ruff))
    assert code == 2
    assert area_fact(out, "code") == "ruff exited 2 · 1 stderr line withheld · not cleared by gitleaks"
    assert "boom" not in out + err


@pytest.mark.parametrize(
    "stderr",
    [
        f"\x1b[1m{FAKE_KEY}\x1b[0m\x1b[2m\n",
        f"\x1b[{FAKE_KEY}\n",
        f"\x1b(B{FAKE_KEY}\n",
        f"\x9b1m{FAKE_KEY}\n",
        f"\x801m{FAKE_KEY}\n",
        f"\x9f1m{FAKE_KEY}\n",
        "\x1b[31mboom\x1b[0m\n",
    ],
    ids=[
        "colour around a key", "a cursor code before a key", "a charset selection", "an 8-bit control",
        "the first 8-bit control", "the last 8-bit control", "a clean coloured line",
    ],
)
def test_a_text_holding_a_terminal_code_is_withheld_without_a_vet(build_repo, served_scanners, elsewhere, capsys, stderr):
    """VERBOSE-14, VERBOSE-24: a text holding ESC or a C1 control is withheld whole and starts no vet."""
    fake_gitleaks(served_scanners, on_scan=vetting('echo "[]" > "$report"; exit 0'))
    ruff = fake_ruff(elsewhere, f"printf '%s' {shlex.quote(stderr)} >&2; exit 2")
    code, out, err = refused(build_repo({"src/a.py": "x = 1\n"}), capsys, locate_ruff=lambda: str(ruff))
    assert code == 2
    assert area_fact(out, "code") == "ruff exited 2 · 1 stderr line withheld · not cleared by gitleaks"
    assert not (served_scanners / "gitleaks.vetted").exists()
    assert FAKE_KEY not in out + err
    assert "boom" not in out + err


@pytest.mark.integration
def test_no_scratch_directory_withholds_the_text(tmp_path, monkeypatch):
    """VERBOSE-17: a vet that cannot make its scratch directory withholds the text."""
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "missing"))
    assert scanners.vetted_tail("boom\n") == " · 1 stderr line withheld · not cleared by gitleaks"


def test_a_clean_run_starts_no_vet(build_repo, served_scanners, elsewhere, capsys):
    """VERBOSE-19: a run without -v whose scanners refuse nothing starts no vet, whatever they wrote to stderr."""
    fake_gitleaks(served_scanners, on_scan=vetting(flagging(FAKE_KEY)))
    noting_on_every_call(served_scanners / scanners.ACTIONLINT)
    noting_on_every_call(served_scanners / scanners.OSV_SCANNER)
    ruff = noting_on_every_call(fake_ruff(elsewhere, "echo '[]'"))
    zizmor = fake_zizmor(elsewhere / "zizmor", "echo '[]'")
    noting_on_every_call(Path(zizmor()))
    code, _, _ = refused(judged_repository(build_repo), capsys, locate_ruff=lambda: str(ruff), locate_zizmor=zizmor)
    assert code == 0
    assert not (served_scanners / "gitleaks.vetted").exists()


def test_a_refusal_without_stderr_starts_no_vet(build_repo, served_scanners, elsewhere, capsys):
    """VERBOSE-22: a refusal that wrote no stderr starts no vet."""
    fake_gitleaks(served_scanners, on_scan=vetting(flagging(FAKE_KEY)))
    ruff = fake_ruff(elsewhere, "exit 2")
    code, out, _ = refused(build_repo({"src/a.py": "x = 1\n"}), capsys, locate_ruff=lambda: str(ruff))
    assert code == 2
    assert area_fact(out, "code") == "ruff exited 2"
    assert not (served_scanners / "gitleaks.vetted").exists()


def test_the_vet_judges_as_the_secret_scan_judges(build_repo, served_scanners, elsewhere, tmp_path, monkeypatch, capsys):
    """VERBOSE-15, VERBOSE-25, VERBOSE-28, VERBOSE-29: the vet reads stdin under quality's config, ignores
    gitleaks:allow, redacts its report and leaves no scratch directory."""
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
    """VERBOSE-14, VERBOSE-15, VERBOSE-16, VERBOSE-17, VERBOSE-18, VERBOSE-25, VERBOSE-26, VERBOSE-27: real gitleaks
    clears a clean text and withholds a key, whole and counted, whatever the text holds around it."""
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
