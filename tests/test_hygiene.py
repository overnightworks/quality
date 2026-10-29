import contextlib
import io
import os
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from quality import cli, hygiene, sonar
from quality.compose import is_compose_file
from quality.report import Finding
from tests.support.engines import fake_engine
from tests.support.hygiene import CLEAN_LINE, ENGINE_RULES, FILE_RULES, assert_refusal, compose
from tests.support.hygiene import hygiene_output as run


def deleted_compose_repo(build_repo, tmp_path):
    repo = build_repo(compose('- "8080:80"'))
    (repo / "compose.yaml").unlink()
    return repo


def truncated_compose_repo(build_repo, tmp_path):
    return build_repo({"compose.yaml": "services: [\n"})


def non_utf8_filename_repo(build_repo, tmp_path):
    return build_repo({"docs/\udcff.txt": "x\n"})


@pytest.mark.parametrize(
    ("arrange", "expected_code", "expected_text"),
    [
        (lambda build_repo, tmp_path: build_repo({}), 0, CLEAN_LINE),
        (non_utf8_filename_repo, 0, CLEAN_LINE),
        (lambda build_repo, tmp_path: tmp_path, 2, "cannot judge"),
        (lambda build_repo, tmp_path: build_repo({".env": "A=1\n"}), 1, "finding(s)"),
        (lambda build_repo, tmp_path: build_repo({"\udcff.env": "A=1\n"}), 1, "env-file \\udcff.env - "),
        (deleted_compose_repo, 0, CLEAN_LINE),
        (truncated_compose_repo, 2, "cannot judge"),
    ],
    ids=[
        "empty-repository",
        "non-utf8-filename",
        "not-a-git-repository",
        "tracked-env-file",
        "cell 9 and #86 cell 7 non-utf8-env-filename-rendered-safely",
        "#76 cell 5 compose deleted from disk is gone",
        "malformed-compose-yaml",
    ],
)
def test_exit_code_tells_clean_from_cannot_judge(build_repo, tmp_path, capsys, arrange, expected_code, expected_text):
    """#3 rule 1: exit 0 clean, 1 findings, 2 cannot judge."""
    code, output = run(capsys, arrange(build_repo, tmp_path))
    assert code == expected_code
    assert expected_text in output


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


@pytest.mark.integration
def test_quality_tracks_no_env_or_compose_file():
    """#3 rule 5: the quality repository passes its own hygiene check and tracks no env or compose file."""
    root = Path(__file__).resolve().parents[1]
    tracked = subprocess.run(["git", "-C", str(root), "ls-files"], capture_output=True, text=True, check=True).stdout
    assert cli.main(["hygiene", str(root)]) == 0
    assert not [name for name in tracked.splitlines() if is_compose_file(name)]
    assert ".env" not in tracked


def test_a_tracked_workflow_missing_from_disk_is_never_handed_to_an_engine(build_repo, clean_engines, capsys):
    """#76 rule 3, cell 10: no workflow engine is installed, so any name passed on would be cannot judge."""
    repo = build_repo({".github/workflows/w.yml": "on: push\n"})
    (repo / ".github/workflows/w.yml").unlink()
    code = cli.main(["hygiene", "--engines", str(clean_engines), str(repo)])
    captured = capsys.readouterr()
    assert (code, "hygiene: clean (" in captured.out) == (0, True), captured.err


def test_a_tracked_env_file_deleted_from_disk_is_still_a_finding(build_repo, capsys):
    """#76 rule 5, cell 14 (E5): the rule judges the index's names, so only `git rm --cached` clears it."""
    repo = build_repo({".env": "A=1\n"})
    (repo / ".env").unlink()
    code, output = run(capsys, repo)
    assert code == 1
    assert_refusal(output, "env-file", ".env")


BASELINE = {"quality/baseline.toml": ""}
NEW_DEBT = "def long_one():\n" + "    x = 1\n" * 61
ENV_FINDING = "env-file .env - "


def config_linked_out_of_the_repository(build_repo, elsewhere):
    repo = build_repo({".env": "A=1\n"})
    (elsewhere / "config.toml").write_text('[gate]\nmandatory_jobs = ["checks"]\n')
    (repo / "quality").mkdir()
    (repo / "quality/config.toml").symlink_to(elsewhere / "config.toml")
    subprocess.run(["git", "-C", str(repo), "add", "quality/config.toml"], check=True)
    return repo


def files(tracked: dict[str, str]):
    return lambda build_repo, elsewhere: build_repo(tracked)


@pytest.mark.parametrize(
    ("arrange", "with_engines", "expected_code", "expected_lines"),
    [
        (files({**BASELINE, ".env": "A=1\n", "src/bad.py": "def broken(:\n"}), False, 2,
         [ENV_FINDING, "hygiene: size unparsable - src/bad.py is not valid Python",
          "hygiene: python-security unparsable - ruff cannot parse src/bad.py",
          "hygiene: python-print unparsable - ruff cannot parse src/bad.py",
          "hygiene: 2 finding(s); verdicts: 10 clean, 2 findings, 3 unparsable"]),
        (config_linked_out_of_the_repository, True, 2,
         [ENV_FINDING, "hygiene: compose-port cannot judge - quality/config.toml resolves outside the repository",
          "hygiene: secret cannot judge - quality/config.toml resolves outside the repository",
          "hygiene: advisory cannot judge - quality/config.toml resolves outside the repository",
          "hygiene: root-layout cannot judge - quality/config.toml resolves outside the repository",
          "hygiene: file-suffix cannot judge - quality/config.toml resolves outside the repository",
          "hygiene: script-name cannot judge - quality/config.toml resolves outside the repository",
          "hygiene: script-header cannot judge - quality/config.toml resolves outside the repository",
          "hygiene: test-layout cannot judge - quality/config.toml resolves outside the repository",
          "hygiene: test-import cannot judge - quality/config.toml resolves outside the repository",
          "hygiene: python-floor cannot judge - quality/config.toml resolves outside the repository",
          "hygiene: 1 finding(s); verdicts: 8 clean, 1 findings, 10 cannot judge"]),
        (files({**BASELINE, "compose.yaml": "services: [\n", "src/a.py": NEW_DEBT}), False, 2,
         ["hygiene: compose-port cannot judge - cannot read compose.yaml", "size src/a.py:1 - function long_one"]),
        (files({".env": "A=1\n", **compose('- "8080:80"')}), False, 1,
         [ENV_FINDING, "compose-port compose.yaml - ", "hygiene: 3 finding(s); verdicts: 12 clean, 3 findings"]),
        (files({}), False, 0,
         [f"{CLEAN_LINE}; skipped without --engines: secret, workflow-lint, workflow-security, advisory"]),
        (files({}), True, 0,
         [f"hygiene: clean ({FILE_RULES}, {ENGINE_RULES});"]),
        (files({"compose-\udcff.yaml": compose('- "8080:80"')["compose.yaml"]}), False, 1,
         ["compose-port compose-\\udcff.yaml - service web publishes port 8080:80"]),
        (files({**BASELINE, "src/\udcff.py": NEW_DEBT}), False, 1, ["size src/\\udcff.py:1 - function long_one"]),
        (files({"src/\udcff.py": "def broken(:\n"}), False, 2,
         ["functions unmeasured: src/\\udcff.py", "hygiene: python-security unparsable - ruff cannot parse src/\\udcff.py",
          "hygiene: python-print unparsable - ruff cannot parse src/\\udcff.py"]),
    ],
    ids=[
        "cell 1 unparsable size hides no env-file finding",
        "cell 2 secret cannot judge hides no env-file finding",
        "cell 3 unreadable compose hides no size finding",
        "cell 4 findings in two rules",
        "cell 5 clean names the file rules",
        "cell 6 clean with engines names all eleven rules",
        "cell 7 non-utf8 compose name",
        "cell 8 non-utf8 python name",
        "rule 4 non-utf8 python name in the size report",
    ],
)
def test_every_rule_is_judged_and_the_exit_is_the_worst(
    build_repo, elsewhere, clean_engines, capsys, arrange, with_engines, expected_code, expected_lines
):
    """#55 rules 1-4, cells 1-8 (cell 9 is the exit-code table's non-utf8 env case): each rule prints its own
    verdict, one rule's refusal hides no other rule's, the exit is the worst verdict's, the clean line names the
    rules, and every path prints backslash-escaped."""
    engine_arguments = ["--engines", str(clean_engines)] if with_engines else []
    code = cli.main(["hygiene", *engine_arguments, str(arrange(build_repo, elsewhere))])
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert code == expected_code, output
    assert [line for line in expected_lines if line not in output] == [], output


def test_a_finding_under_a_rule_its_check_does_not_judge_fails_loud(build_repo, monkeypatch):
    """#55 rule 2: a finding under a rule the check was not given is never dropped into a clean exit."""
    foreign = Finding("workflow-other", ".env", None, "an unlisted rule", "none")
    monkeypatch.setattr(hygiene, "check_env_files", lambda tracked: [foreign])
    arguments = ["hygiene", str(build_repo({}))]
    with pytest.raises(ValueError, match="workflow-other"):
        cli.main(arguments)


def test_a_refusing_workflow_engine_stops_no_other_workflow_rule(build_repo, clean_engines, capsys):
    """#55 rule 1: actionlint cannot judge, yet zizmor still runs and its finding prints under its own rule."""
    zizmor_report = Path(__file__).parent / "fixtures" / "engine-outputs" / "zizmor-finding.json"
    fake_engine(clean_engines, "actionlint", on_scan="echo boom >&2; exit 3")
    fake_engine(clean_engines, "zizmor", on_scan=f"cat '{zizmor_report}'; exit 12")
    repo = build_repo({".github/workflows/w.yml": "on: push\n"})
    code = cli.main(["hygiene", "--engines", str(clean_engines), str(repo)])
    captured = capsys.readouterr()
    assert code == 2
    assert "hygiene: workflow-lint cannot judge - actionlint exited 3" in captured.err
    assert "workflow-security .github/workflows/w.yml:7 - zizmor unpinned-uses" in captured.out


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


MODULE_DOOR = [sys.executable, "-m", "quality.cli"]
CONSOLE_SCRIPT_DOOR = [str(Path(sys.executable).parent / "quality")]


def quality_door(*arguments: str, cwd: Path, door: list[str] = MODULE_DOOR, **streams) -> subprocess.CompletedProcess[str]:
    return subprocess.run([*door, *arguments], cwd=cwd, text=True, check=False, **streams)


@pytest.fixture
def env_and_broken_compose(build_repo) -> Path:
    return build_repo({".env": "A=1\n", "compose.yaml": "services: [\n"})


@pytest.mark.parametrize("door", [MODULE_DOOR, CONSOLE_SCRIPT_DOOR], ids=["module", "console script"])
def test_a_merged_pipe_shows_the_hygiene_lines_in_run_order(env_and_broken_compose, door):
    """#86 rule 3, cells 10 and 12a: reports, the finding with its fix, `cannot judge`, then the summary."""
    done = quality_door(
        "hygiene", ".", cwd=env_and_broken_compose, door=door, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    )
    prefixes = ["size: ", "clone: ", "import-contract: ", "env-file .env - ", "  Fix: ", "hygiene: compose-port cannot judge - ", "root-layout .env - ", "  Fix: ", "hygiene: 2 finding(s)"]
    lines = done.stdout.splitlines()
    assert all(line.startswith(prefix) for line, prefix in zip(lines, prefixes, strict=True)), done.stdout


def test_discarding_stderr_hides_exactly_the_cannot_judge_line(env_and_broken_compose):
    """#86 rule 3, cell 11: no line changes its stream; the exit stays 2."""
    done = quality_door("hygiene", ".", cwd=env_and_broken_compose, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    assert (done.returncode, "compose-port cannot judge" in done.stdout, "env-file .env" in done.stdout) == (2, False, True)


SONAR_FINDINGS_SCRIPT = """
import sys
sys.path.insert(0, {repository_root!r})
from tests.support.sonar import issues_page, sonar_answering
from quality import cli
cli.run(lambda: cli.main(["sonar", "findings"], transport=sonar_answering(issues=issues_page(2, 1, 2)), sleep=lambda seconds: None))
"""


@pytest.mark.integration
def test_a_merged_pipe_lists_the_sonar_findings_before_the_red_summary(tmp_path):
    """#86 rule 3, cell 12: the findings precede the summary through `2>&1`."""
    from tests.support.sonar import PULL_REQUEST_ENVIRONMENT, TASK_FIXTURE, legacy_config_absent

    (tmp_path / "quality").mkdir()
    (tmp_path / "quality" / "config.toml").write_text(legacy_config_absent)
    (tmp_path / sonar.TASK_FILE).parent.mkdir()
    (tmp_path / sonar.TASK_FILE).write_text(TASK_FIXTURE.read_text())
    script = tmp_path / "findings.py"
    script.write_text(textwrap.dedent(SONAR_FINDINGS_SCRIPT).format(repository_root=str(Path(__file__).parents[1])))
    done = subprocess.run(
        [sys.executable, str(script)], cwd=tmp_path, text=True, check=False, env={**os.environ, **PULL_REQUEST_ENVIRONMENT},
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    prefixes = ["sonar src/a1.py:1 - ", "  Fix: ", "sonar src/a2.py:2 - ", "  Fix: ", "sonar: new 2"]
    lines = done.stdout.splitlines()
    assert (done.returncode, all(line.startswith(prefix) for line, prefix in zip(lines, prefixes, strict=True))) == (1, True), done.stdout


def test_a_replaced_stdout_still_prints_the_help():
    """#86 rule 3: `main` leaves the process's streams alone, so a captured stdout works."""
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured), pytest.raises(SystemExit) as stopped:
        cli.main(["--help"])
    assert (stopped.value.code, "hygiene" in captured.getvalue()) == (0, True)


@pytest.mark.integration
def test_a_closed_stdout_stream_is_cannot_judge_with_its_diagnostic(tmp_path, capsys, monkeypatch):
    """#86 rule 3: a closed stdout object never crashes `main`; the diagnostic reaches stderr."""
    monkeypatch.setenv("NO_COLOR", "1")  # Python 3.14's argparse otherwise probes the closed stdout for colour
    closed = io.TextIOWrapper(io.BytesIO())
    closed.close()
    with contextlib.redirect_stdout(closed):
        code = cli.main(["hygiene", str(tmp_path / "missing")])
    assert (code, "hygiene: cannot judge" in capsys.readouterr().err) == (2, True)


@pytest.mark.integration
def test_a_closed_stdout_is_cannot_judge_and_never_a_crash(tmp_path):
    """#86 rule 3: with fd 1 closed the door still exits 2 with its cannot-judge line."""
    done = subprocess.run(
        ["bash", "-c", '"$PY" -m quality.cli hygiene "$MISSING" >&-'],
        env={**os.environ, "PY": sys.executable, "MISSING": str(tmp_path / "missing")},
        text=True, check=False, stderr=subprocess.PIPE,
    )
    assert (done.returncode, "hygiene: cannot judge" in done.stderr) == (2, True), done.stderr
