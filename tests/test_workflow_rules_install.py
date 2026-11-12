import json
from collections.abc import Callable

import pytest

from quality.report import CannotJudge, FindingEvent
from quality.rules import Rule
from quality.workflow_rules import check_runtime_install
from tests.support.check import check_output as run
from tests.support.check import finding_rows, is_clean, rule_findings

INSTALLS, INVALID = FindingEvent.INSTALLS, FindingEvent.INVALID
WORKFLOW = ".github/workflows/w.yml"
DOWNLOAD = "a download runs as a shell script"
UNREADABLE = "command cannot be read to its end"
BUILT_AT_RUN_TIME = "command is built at run time, an install cannot be read"
FIX = (
    "take the tool from the lockfile, the runner image or an image pinned by digest, "
    "or explain on the line above: # runtime-install: <reason>"
)
MARKER = "# runtime-install: proves the install on a fresh system"
BLOCK_LINE = 10
QUOTED_LINE = 8


def steps_of(on: str = "push") -> str:
    """A workflow up to its `steps:` key (line 7); the next line is line 8."""
    return f"on: {on}\npermissions: {{}}\njobs:\n  j:\n    runs-on: ubuntu-latest\n    timeout-minutes: 15\n    steps:\n"


def block_script(*lines: str, on: str = "push") -> str:
    """One step whose `run: |` is line 9 and whose script starts on line 10."""
    script = "\n".join(f"          {line}" for line in lines)
    return f"{steps_of(on)}      - name: t\n        run: |\n{script}\n"


def quoted_script(line: str, on: str = "push") -> str:
    """One step whose quoted `run:` value is line 8."""
    return steps_of(on) + f"      - run: {json.dumps(line)}\n"


@pytest.fixture(params=[(block_script, BLOCK_LINE), (quoted_script, QUOTED_LINE)], ids=["literal block", "quoted"])
def scalar(request) -> tuple[Callable[[str], str], int]:
    """How a one-line script stands in a workflow, and the line of its finding."""
    return request.param


def judged(build_repo, text: str):
    return rule_findings(build_repo({WORKFLOW: text}), Rule.RUNTIME_INSTALL)


def shape(findings):
    return [(finding.event, finding.line, finding.fact) for finding in findings]


INSTALL_ROWS = [
    ("apt-get install -y x", "apt-get install"),
    ("apt install x", "apt install"),
    ("sudo apt-get install -y x", "apt-get install"),
    ("sudo -E apt-get -y install x", "apt-get install"),
    ("apt-get -o Acquire::Retries=3 install x", "apt-get install"),
    ("apt-get update && apt-get install -y x", "apt-get install"),
    ("apt-get upgrade -y", "apt-get upgrade"),
    ("dpkg -i p.deb", "dpkg -i"),
    ("sudo dpkg --install p.deb", "dpkg --install"),
    ("dnf install -y x", "dnf install"),
    ("yum install -y x", "yum install"),
    ("yum update -y", "yum update"),
    ("apk add --no-cache x", "apk add"),
    ("brew install x", "brew install"),
    ("brew bundle", "brew bundle"),
    ("snap install x --classic", "snap install"),
    ("pacman -S x", "pacman -S"),
    ("pacman -Syu", "pacman -Syu"),
    ("zypper install x", "zypper install"),
    ("zypper in x", "zypper in"),
    ("choco install x", "choco install"),
    ("winget install x", "winget install"),
    ("port install x", "port install"),
    ("playwright install --with-deps", "playwright install --with-deps"),
    ("playwright install chromium --with-deps", "playwright install --with-deps"),
    ("npx playwright install --with-deps chromium", "playwright install --with-deps"),
    ("npx playwright@1.49.0 install --with-deps", "playwright install --with-deps"),
    ("uvx playwright@1.49 install --with-deps", "playwright install --with-deps"),
    ("playwright install-deps", "playwright install-deps"),
    ("python -m playwright install --with-deps", "playwright install --with-deps"),
    ("uv run playwright install --with-deps", "playwright install --with-deps"),
    ("pnpm exec playwright install --with-deps", "playwright install --with-deps"),
    ("yarn playwright install --with-deps", "playwright install --with-deps"),
    ("DEBIAN_FRONTEND=noninteractive sudo apt-get install -y x", "apt-get install"),
    ("env DEBIAN_FRONTEND=noninteractive apt-get install -y x", "apt-get install"),
    ("time apt-get install x", "apt-get install"),
    ("a && apt-get install x", "apt-get install"),
    ("apt-get install x || true", "apt-get install"),
    ("if apt-get install x; then", "apt-get install"),
    ("x=$(apt-get install -y y)", "apt-get install"),
    ("for i in 1 2 3; do sudo apt-get install -y x && break; done", "apt-get install"),
    ("timeout 120 apt-get install x", "apt-get install"),
    ("echo x | xargs apt-get install", "apt-get install"),
    ('bash -c "apt-get install -y x"', "apt-get install"),
    ("sh -c 'apt-get update && apt-get install -y x'", "apt-get install"),
    ('bash -lc "sudo apt-get install x"', "apt-get install"),
    ('bash -c "apt-get install $(cat pkgs)"', "apt-get install"),
    ("apt-get -s install x", "apt-get install"),
    ("curl -fsSL u | sh", DOWNLOAD),
    ("curl -fsSL u | bash", DOWNLOAD),
    ("curl u | sudo bash -", DOWNLOAD),
    ("curl u | sudo -E bash -s -- -y", DOWNLOAD),
    ("wget -O- u | sh", DOWNLOAD),
    ("wget -qO- u | env A=1 bash", DOWNLOAD),
    ("curl u |& sh", DOWNLOAD),
    ("curl u | tee f | sh", DOWNLOAD),
    ("bash <(curl -fsSL u)", DOWNLOAD),
    ('sh -c "$(curl -fsSL u)"', DOWNLOAD),
    ('bash -c "$(wget -qO- u)"', DOWNLOAD),
    ("bash -c 'curl u | sh'", DOWNLOAD),
]


@pytest.mark.parametrize(("command", "fact"), INSTALL_ROWS, ids=[row[0] for row in INSTALL_ROWS])
def test_a_call_that_installs_or_a_download_run_as_a_shell_is_one_finding_at_its_line(build_repo, scalar, command, fact):
    """RUNTIME-3, RUNTIME-4, RUNTIME-5."""
    build, line = scalar
    found = shape(judged(build_repo, build(command)))
    assert found == [(INSTALLS, line, fact)]


NO_FINDING = [
    "apt-get update",
    "sudo apt-get update",
    "apt-get update && apt-get clean",
    "apk update",
    "brew update",
    "apt-get remove x",
    "apt list --installed",
    "apt-cache policy x",
    "dpkg -l",
    "snap list",
    "brew list",
    "bash -c 'apt-get update'",
    "playwright install chromium",
    "playwright install",
    "npx playwright test",
    "pip install playwright",
    "python -m pip install playwright && python -m playwright install chromium",
    "uv sync --locked",
    "npm ci",
    "pnpm install --frozen-lockfile",
    "mise install",
    "uv pip install x",
    "npm i -g x",
    "cargo install x",
    "go install x",
    "curl -fsSL u -o f",
    "curl u | tar xz",
    "curl u | jq .",
    "curl u -o f && sh f",
    "curl u; sh",
    "bash script.sh",
    "./install.sh",
    'echo "sudo apt-get install x"',
    "command -v brew",
    "which apt-get",
    "# apt-get install x",
    "RUN apt-get install -y x",
    "bash run.sh -c 'apt-get install x'",
    'echo "it listens on port 8080',
    "xvfb-run apt-get install x",
    "docker run img apt-get install x",
    "make install-deps",
    "sudo -nu ci apt-get install x",
    "yarn dlx playwright install --with-deps",
    "pnpm playwright install --with-deps",
    "bun x playwright install --with-deps",
    "$TOOL install x",
    "echo | bash -c 'curl u' | sh",
    "bash -c 'curl u >/dev/null' | sh",
]


@pytest.mark.parametrize("command", NO_FINDING)
def test_a_command_that_installs_nothing_or_is_a_named_gap_is_no_finding(build_repo, scalar, command):
    """RUNTIME-3, RUNTIME-4, RUNTIME-5, RUNTIME-6."""
    build, _ = scalar
    assert judged(build_repo, build(command)) == []


INVALID_ROWS = [
    ("PM=apt-get; sudo $PM install -y x", BUILT_AT_RUN_TIME),
    ('eval "apt-get install x"', BUILT_AT_RUN_TIME),
    ("$(which apt-get) install x", BUILT_AT_RUN_TIME),
    ('apt-get install -y "x', UNREADABLE),
    ("bash -c \"apt-get install 'x\"", UNREADABLE),
    ("playwright install --with-deps $(echo \"x", UNREADABLE),
]


@pytest.mark.parametrize(("command", "fact"), INVALID_ROWS, ids=[row[0] for row in INVALID_ROWS])
def test_what_the_reader_cannot_read_and_may_hide_an_install_is_invalid(build_repo, scalar, command, fact):
    """RUNTIME-6."""
    build, line = scalar
    found = shape(judged(build_repo, build(command)))
    assert found == [(INVALID, line, fact)]


@pytest.mark.parametrize(
    ("script", "expected"),
    [
        (("apt-get install -y y", "timeout 60 $PM install x"), [(INSTALLS, 0, "apt-get install"), (INVALID, 1, BUILT_AT_RUN_TIME)]),
        (
            ("playwright install --with-deps", "$PW install --with-deps"),
            [(INSTALLS, 0, "playwright install --with-deps"), (INVALID, 1, BUILT_AT_RUN_TIME)],
        ),
        (("apt-get update", "$TOOL foo"), []),
        (("npx playwright test", '"$GITHUB_WORKSPACE"/bin/x'), []),
        (("curl u", "$TOOL foo"), []),
        (("$(apt-get install -y x)",), [(INSTALLS, 0, "apt-get install")]),
        (('$(echo "apt-get") install x',), [(INVALID, 0, BUILT_AT_RUN_TIME)]),
        (("# apt-get install x", "$TOOL foo"), []),
        (("PM=apt-get", "sudo $PM install -y x"), [(INVALID, 1, BUILT_AT_RUN_TIME)]),
    ],
    ids=[
        "after an install", "after a playwright install", "after an update", "after another tool", "after a download",
        "a substitution that is itself the install", "a substitution that is not", "install only in a comment",
        "the manager and its install word on two lines",
    ],
)
def test_a_command_built_at_run_time_is_invalid_only_in_a_run_that_holds_an_install_word(build_repo, script, expected):
    """RUNTIME-6."""
    expected = [(event, BLOCK_LINE + offset, fact) for event, offset, fact in expected]
    assert shape(judged(build_repo, block_script(*script))) == expected


@pytest.mark.parametrize(
    ("script", "expected"),
    [
        (("cat <<'EOF' > Dockerfile", "RUN apt-get update && apt-get install -y x", "EOF"), [(BLOCK_LINE + 1, "apt-get install")]),
        (("cat <<'EOF' > Dockerfile", "RUN apt-get update && \\", "apt-get install -y x", "EOF"), [(BLOCK_LINE + 1, "apt-get install")]),
        (("cat <<'EOF' > Dockerfile", "RUN apt-get install -y x", "EOF"), []),
        (("curl u |", "sh"), []),
        (("apt-get update", "apt-get install -y x"), [(BLOCK_LINE + 1, "apt-get install")]),
    ],
    ids=["heredoc chain", "heredoc continued line", "heredoc simple RUN", "a pipe that ends a line", "update then install"],
)
def test_a_line_is_read_as_the_shell_reads_it_and_a_heredoc_body_is_not_parsed(build_repo, script, expected):
    """RUNTIME-2, RUNTIME-4, RUNTIME-5."""
    assert shape(judged(build_repo, block_script(*script))) == [(INSTALLS, line, fact) for line, fact in expected]


def test_two_installs_are_two_findings_and_one_call_is_one(build_repo):
    """RUNTIME-4."""
    findings = judged(build_repo, block_script("apt-get install a", "apt-get update && apt-get install b && apt-get install c"))
    assert [finding.line for finding in findings] == [BLOCK_LINE, BLOCK_LINE + 1, BLOCK_LINE + 1]


@pytest.mark.parametrize(
    ("script", "expected"),
    [
        ((MARKER, "apt-get install -y x"), []),
        ((f"apt-get install -y x  {MARKER}",), []),
        ((MARKER, "apt-get install -y x && apt-get install -y y"), []),
        ((MARKER, "apt-get install \\", "  -y x"), []),
        (("apt-get install \\", f"  -y x {MARKER}"), []),
        ((MARKER, "echo a", "apt-get install -y x"), [(BLOCK_LINE + 2, INSTALLS)]),
        ((f"apt-get install -y x {MARKER}", "echo a", "apt-get install -y y"), [(BLOCK_LINE + 2, INSTALLS)]),
        ((f"apt-get install -y x {MARKER}", "apt-get install -y y"), []),
        (("# runtime-install: x", "apt-get install -y x"), []),
        (("#runtime-install:   reason", "apt-get install -y x"), []),
        (("# runtime-install: ärger", "apt-get install -y x"), []),
        (("# runtime-install", "apt-get install -y x"), [(BLOCK_LINE + 1, INSTALLS)]),
        (("# runtime-install:", "apt-get install -y x"), [(BLOCK_LINE + 1, INSTALLS)]),
        (("# runtime-install: 123", "apt-get install -y x"), [(BLOCK_LINE + 1, INSTALLS)]),
        (("# runtime-install: -", "apt-get install -y x"), [(BLOCK_LINE + 1, INSTALLS)]),
        (("# runtime-installs: x", "apt-get install -y x"), [(BLOCK_LINE + 1, INSTALLS)]),
        (("# pytest-workers: x", "apt-get install -y x"), [(BLOCK_LINE + 1, INSTALLS)]),
    ],
    ids=[
        "above", "on its line", "above two calls", "above a continued command", "on the last line of a continued command",
        "above a later line", "on the first call's line only", "on the line above a second call", "short reason",
        "wide spaces", "a letter of any script", "no colon", "no reason", "no letter", "dash only", "another id",
        "the other rule's id",
    ],
)
def test_a_marker_on_a_calls_line_or_directly_above_clears_that_call(build_repo, script, expected):
    """RUNTIME-7."""
    assert [(f.line, f.event) for f in judged(build_repo, block_script(*script))] == expected


def test_a_marker_on_the_run_line_of_a_block_covers_its_first_line_only(build_repo):
    """RUNTIME-7."""
    text = steps_of() + f"      - name: t\n        run: | {MARKER}\n          apt-get install -y x\n          apt-get install -y y\n"
    assert shape(judged(build_repo, text)) == [(INSTALLS, BLOCK_LINE + 1, "apt-get install")]


def test_a_marker_between_the_name_and_the_run_clears_nothing(build_repo):
    """RUNTIME-7."""
    text = steps_of() + f"      - name: t\n        {MARKER}\n        run: |\n          apt-get install -y x\n"
    assert shape(judged(build_repo, text)) == [(INSTALLS, BLOCK_LINE + 1, "apt-get install")]


@pytest.mark.parametrize(
    "step",
    [
        f"      {MARKER}\n      - name: t\n        run: |\n          apt-get update\n          apt-get install -y x\n",
        f"      {MARKER}\n      - name: t\n        run: apt-get install -y x\n",
        f"      {MARKER}\n      - run: apt-get install -y x\n",
        f"      - run: 'true'\n      {MARKER}\n      - name: t\n        run: |\n          apt-get install -y x\n",
        f"      - name: a\n        run: |\n          echo a\n      {MARKER}\n      - name: t\n        run: curl u | sh\n",
    ],
    ids=[
        "a block of two calls", "a plain value behind a name", "a plain value on the step line", "after the previous step",
        "after a block scalar",
    ],
)
def test_a_marker_in_the_dash_column_above_a_step_clears_every_call_of_the_step(build_repo, step):
    """RUNTIME-7."""
    assert judged(build_repo, steps_of() + step) == []


@pytest.mark.parametrize(
    ("step", "line"),
    [
        (f"      - name: a\n        run: |\n          echo a\n          {MARKER}\n      - name: t\n        run: |\n          apt-get install -y x\n", 14),
        (f"      - name: a\n        run: |\n          echo a\n        {MARKER}\n      - name: t\n        run: |\n          apt-get install -y x\n", 14),
        (f"      {MARKER}\n      -\n        name: t\n        run: apt-get install -y x\n", 11),
        (f"      - name: a\n        run: |\n          echo a\n          {MARKER}\n      - run: apt-get install -y x\n", 12),
        (f"      - name: a\n        run: |\n          echo a\n        {MARKER}\n      - run: 'apt-get install -y x'\n", 12),
    ],
    ids=["deeper column", "run column", "a dash that stands alone", "a one-line step under a deeper column", "a quoted one-line step under the run column"],
)
def test_a_marker_in_another_column_or_above_a_dash_that_stands_alone_clears_nothing(build_repo, step, line):
    """RUNTIME-7."""
    assert shape(judged(build_repo, steps_of() + step)) == [(INSTALLS, line, "apt-get install")]


def test_a_marker_on_the_line_of_a_one_line_step_clears_it(build_repo):
    """RUNTIME-7."""
    assert judged(build_repo, steps_of() + f"      - run: apt-get install -y x  {MARKER}\n") == []


def test_a_marker_above_the_first_step_of_two_clears_that_step_only(build_repo):
    """RUNTIME-7."""
    text = steps_of() + f"      {MARKER}\n      - name: t\n        run: |\n          apt-get install -y x\n      - name: u\n        run: |\n          apt-get install -y y\n"
    assert shape(judged(build_repo, text)) == [(INSTALLS, 14, "apt-get install")]


@pytest.mark.parametrize(
    ("steps", "line"),
    [
        ('steps: [{name: t,\n      run: "apt-get install x"}]', 7),
        ('steps: [{run: "apt-get install x"}]', 6),
    ],
    ids=["over two lines", "on one line"],
)
def test_a_flow_step_has_no_step_form(build_repo, steps, line):
    """RUNTIME-7."""
    text = f"on: push\njobs:\n  j:\n    runs-on: x\n    {MARKER}\n    {steps}\n"
    assert shape(judged(build_repo, text)) == [(INSTALLS, line, "apt-get install")]


def test_the_ids_of_the_two_markers_are_not_interchangeable(build_repo):
    """RUNTIME-7: a `runtime-install` marker clears no pytest call, and a `pytest-workers` marker above a step clears no install."""
    repo = build_repo({WORKFLOW: block_script("# runtime-install: reason", "pytest")})
    assert [finding.event for finding in rule_findings(repo, Rule.PYTEST_WORKERS)] == [FindingEvent.SERIAL]
    above_step = steps_of() + "      # pytest-workers: reason\n      - name: t\n        run: |\n          pytest\n"
    repo = build_repo({WORKFLOW: above_step})
    assert [finding.event for finding in rule_findings(repo, Rule.PYTEST_WORKERS)] == [FindingEvent.SERIAL]


@pytest.mark.parametrize("on", ["push", "schedule", "workflow_call", "pull_request"])
def test_a_workflow_is_judged_whatever_its_trigger(build_repo, on):
    """RUNTIME-1."""
    assert shape(judged(build_repo, block_script("apt-get install x", on=on))) == [(INSTALLS, BLOCK_LINE, "apt-get install")]


def test_a_run_is_read_as_shell_whatever_its_shell(build_repo):
    """RUNTIME-1."""
    text = steps_of() + "      - shell: python\n        run: apt-get install x\n"
    assert shape(judged(build_repo, text)) == [(INSTALLS, QUOTED_LINE + 1, "apt-get install")]


def test_only_the_run_of_a_step_is_read(build_repo):
    """RUNTIME-1: names, conditions, inputs, environments and the steps of `uses:` are not scripts."""
    step = (
        "      - name: apt-get install x\n        if: contains(github.ref, 'apt-get install')\n        env:\n"
        "          CMD: apt-get install x\n        run: 'true'\n"
        "      - uses: a/b@0123456789012345678901234567890123456789\n        with:\n          command: apt-get install x\n"
    )
    assert judged(build_repo, steps_of() + step) == []


def test_a_composite_action_is_not_read(build_repo):
    """RUNTIME-1: the named gap."""
    action = "name: a\nruns:\n  using: composite\n  steps:\n    - shell: bash\n      run: apt-get install x\n"
    repo = build_repo({"action.yml": action, ".github/actions/a/action.yml": action})
    assert rule_findings(repo, Rule.RUNTIME_INSTALL) == []


def test_every_job_and_step_is_read_and_each_finding_names_its_file(build_repo):
    """RUNTIME-1, RUNTIME-8."""
    text = steps_of() + "      - run: 'true'\n      - run: apt-get install x\n  k:\n    runs-on: ubuntu-latest\n    steps:\n      - run: brew install y\n"
    repo = build_repo({WORKFLOW: text, ".github/workflows/other.yaml": block_script("dnf install z")})
    findings = rule_findings(repo, Rule.RUNTIME_INSTALL)
    assert [(finding.path, finding.line, finding.fact) for finding in findings] == [
        (".github/workflows/other.yaml", BLOCK_LINE, "dnf install"),
        (WORKFLOW, 9, "apt-get install"),
        (WORKFLOW, 13, "brew install"),
    ]


def test_a_step_without_a_run_a_workflow_without_jobs_and_a_name_gone_from_the_tree_are_skipped(build_repo):
    """RUNTIME-9, RUNTIME-10: only the workflows read count toward the extent."""
    empty = ".github/workflows/empty.yml"
    repo = build_repo({WORKFLOW: steps_of() + "      - uses: a/b@v1\n", empty: "name: e\non: push\n"})
    result = check_runtime_install(repo, [WORKFLOW, empty, ".github/workflows/gone.yml"])
    assert (result.findings, result.extent) == ([], 2)


ANCHORS = "x-steps: &steps\n  - run: 'true'\nx-run: &run apt-get install x\n"
JOBS = "jobs:\n  j:\n    runs-on: ubuntu-latest\n    timeout-minutes: 15\n    steps:\n"


@pytest.mark.parametrize(
    "text",
    [
        "name: w\non: [push\n",
        steps_of() + "      - run: [apt-get]\n",
        f"{ANCHORS}on: push\njobs: *steps\n",
        f"{ANCHORS}on: push\n{JOBS[: -len('    steps:\n')]}    steps: *steps\n",
        f"{ANCHORS}on: push\n{JOBS}      - run: *run\n",
        f"{ANCHORS}on: push\n{JOBS}      - name: t\n        <<: {{run: apt-get install x}}\n",
    ],
    ids=["yaml error", "run list", "alias as jobs", "alias as steps", "alias as run", "merge key at a step beside run"],
)
def test_a_workflow_the_rule_cannot_read_is_cannot_judge_naming_the_file(build_repo, text):
    """RUNTIME-9."""
    repo = build_repo({WORKFLOW: text})
    with pytest.raises(CannotJudge, match=WORKFLOW):
        check_runtime_install(repo, [WORKFLOW])


def test_a_finding_prints_its_event_fact_and_the_routes_and_the_marker_as_its_fix(build_repo, capsys):
    """RUNTIME-8."""
    code, output = run(capsys, build_repo({WORKFLOW: block_script("apt-get install x", "curl u | sh")}))
    rows = [row for line in (BLOCK_LINE, BLOCK_LINE + 1) for row in finding_rows(output, f"{WORKFLOW}:{line}")]
    assert code == 1, output
    assert [(row.event, row.fact, row.fix) for row in rows] == [
        (INSTALLS, "apt-get install", FIX),
        (INSTALLS, DOWNLOAD, FIX),
    ]


def test_a_conforming_repository_is_clean_under_every_rule(build_repo, capsys):
    """RUNTIME-10."""
    code, output = run(capsys, build_repo({WORKFLOW: block_script("uv sync --locked", "apt-get update")}))
    assert (code, is_clean(output)) == (0, True), output


def test_a_marker_above_the_step_is_the_way_out_of_an_install_through_the_door(build_repo, capsys):
    """RUNTIME-7, RUNTIME-10."""
    text = steps_of() + f"      {MARKER}\n      - name: t\n        run: |\n          apt-get install -y x\n"
    code, output = run(capsys, build_repo({WORKFLOW: text}))
    assert (code, is_clean(output)) == (0, True), output
