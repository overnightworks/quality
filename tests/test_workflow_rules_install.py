"""What only `runtime-install` decides: which commands install, which Python tools install outside a lock and which downloads run as a shell. Every mechanic it shares
with `pytest-workers` is in test_workflow_rules_script.py."""

import pytest

from quality.report import FindingEvent
from quality.rules import Rule
from tests.support.check import event_line_fact, judge_workflow, rule_findings
from tests.support.workflow_scripts import BLOCK_LINE, QUOTED_LINE, WORKFLOW, block_run, steps_of

pytestmark = pytest.mark.integration

judged = judge_workflow(Rule.RUNTIME_INSTALL)

INSTALLS, INVALID = FindingEvent.INSTALLS, FindingEvent.INVALID
DOWNLOAD = "a download runs as a shell script"
UNREADABLE = "command cannot be read to its end"
BUILT_AT_RUN_TIME = "command is built at run time, an install cannot be read"
MARKER = "# runtime-install: proves the install on a fresh system"


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
    ("pip install x", "pip install"),
    ("pip3 install -r requirements.txt", "pip install"),
    ("pip install --require-hashes -r requirements.txt x", "pip install"),
    ("pip install x==1.2.3", "pip install"),
    ("python -m pip install playwright", "pip install"),
    ("python3.14 -m pip install --upgrade pip", "pip install"),
    ("uv run python -m pip install x", "pip install"),
    ("sudo pip install x", "pip install"),
    ("timeout 60 pip install x", "pip install"),
    ('bash -c "pip install x"', "pip install"),
    ("uv pip install x", "uv pip install"),
    ("uv --quiet pip install x", "uv pip install"),
    ("uv pip install -r requirements.txt", "uv pip install"),
    ("uv pip install --require-hashes x==1.2.3", "uv pip install"),
    ("uv pip install --require-hashes -r requirements.txt x", "uv pip install"),
    ("uv pip install --require-hashes git+https://h/r@v1", "uv pip install"),
    ("uv pip install --require-hashes dist/x-1-py3-none-any.whl", "uv pip install"),
    ("uv pip install --require-hashes -e .", "uv pip install"),
    ("uv pip install --resolution lowest-direct -e . --group dev", "uv pip install"),
    ('uv pip install --python .venv/bin/python "x[extra] @ git+https://h/r@v0.4.0"', "uv pip install"),
    ("pipx install x", "pipx install"),
    ("pipx run x", "pipx run"),
    ("python -m pipx install x", "pipx install"),
    ("uv tool install x", "uv tool install"),
    ("uv tool run x", "uv tool run"),
    ("uvx x", "uvx"),
    ("uvx --no-build check-jsonschema@0.39.0", "uvx"),
    ("uvx playwright test", "uvx"),
    ("uv run --with packaging python check.py", "uv run --with"),
    ("uv run --locked --with=packaging check.py", "uv run --with"),
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
    """RUNTIME-3, RUNTIME-4, RUNTIME-5, RUNTIME-11, RUNTIME-12, RUNTIME-13."""
    build, line = scalar
    found = event_line_fact(judged(build_repo, build(command)))
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
    "uv sync --locked",
    "npm ci",
    "pnpm install --frozen-lockfile",
    "mise install",
    "npm i -g x",
    "uv sync",
    "uv lock",
    "uv run pytest",
    "uv run --locked pytest -q",
    "uv run python -m pytest",
    "uv python install 3.14",
    "uv build",
    "uv pip list",
    "uv tool list",
    "uv tool uninstall x",
    "pip uninstall x",
    "pip list",
    "pip freeze",
    "pipx list",
    "echo uvx x",
    "pip install --require-hashes -r requirements.txt",
    "python -m pip install --require-hashes --no-deps -r requirements.txt",
    "uv pip install --no-build --require-hashes -r tracked/requirements.txt",
    "uv pip install --require-hashes --requirement=requirements.txt",
    "uv pip install --python .venv/bin/python --require-hashes -r a.txt -r b.txt",
    "uv pip install --require-hashes -c constraints.txt -r requirements.txt",
    "npm install -g x",
    "yarn global add x",
    "pnpm add x",
    "gem install x -v 13.0.0",
    "bundle add x",
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
    "| bash | pass |",
    "|& sh",
    "| sh",
]


@pytest.mark.parametrize("command", NO_FINDING)
def test_a_command_that_installs_nothing_or_is_a_named_gap_is_no_finding(build_repo, scalar, command):
    """RUNTIME-3, RUNTIME-4, RUNTIME-5, RUNTIME-6, RUNTIME-11, RUNTIME-12, RUNTIME-13, RUNTIME-14."""
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
    found = event_line_fact(judged(build_repo, build(command)))
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
    assert event_line_fact(judged(build_repo, block_run(*script))) == expected


@pytest.mark.parametrize(
    ("script", "expected"),
    [
        (("cat <<'EOF' > Dockerfile", "RUN apt-get update && apt-get install -y x", "EOF"), [(BLOCK_LINE + 1, "apt-get install")]),
        (("cat <<'EOF' > Dockerfile", "RUN apt-get update && \\", "apt-get install -y x", "EOF"), [(BLOCK_LINE + 1, "apt-get install")]),
        (("cat <<'EOF' > Dockerfile", "RUN apt-get install -y x", "EOF"), []),
        (("curl u |", "sh"), []),
        (("curl u", "| sh"), []),
        (("echo hi", "| bash | pass |"), []),
        (("apt-get update", "apt-get install -y x"), [(BLOCK_LINE + 1, "apt-get install")]),
    ],
    ids=["heredoc chain", "heredoc continued line", "heredoc simple RUN", "a pipe that ends a line", "a pipe that starts a line after a download", "a table row after a command", "update then install"],
)
def test_a_line_is_read_as_the_shell_reads_it_and_a_heredoc_body_is_not_parsed(build_repo, script, expected):
    """RUNTIME-2, RUNTIME-4, RUNTIME-5."""
    assert event_line_fact(judged(build_repo, block_run(*script))) == [(INSTALLS, line, fact) for line, fact in expected]


def test_two_installs_are_two_findings_and_one_call_is_one(build_repo):
    """RUNTIME-4."""
    findings = judged(build_repo, block_run("apt-get install a", "apt-get update && apt-get install b && apt-get install c"))
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
        ((MARKER, "# a plain comment", "apt-get install -y x"), []),
        ((MARKER, "echo a", "# a plain comment", "apt-get install -y x"), [(BLOCK_LINE + 3, INSTALLS)]),
        ((f"apt-get install -y x {MARKER}", "echo a", "apt-get install -y y"), [(BLOCK_LINE + 2, INSTALLS)]),
        ((f"apt-get install -y x {MARKER}", "apt-get install -y y"), [(BLOCK_LINE + 1, INSTALLS)]),
        (("# runtime-install: x", "apt-get install -y x"), []),
        (("#runtime-install:   reason", "apt-get install -y x"), []),
        (("# runtime-install: ärger", "apt-get install -y x"), []),
        (("# runtime-install", "apt-get install -y x"), [(BLOCK_LINE + 1, INSTALLS)]),
        (("# runtime-install:", "apt-get install -y x"), [(BLOCK_LINE + 1, INSTALLS)]),
        (("# runtime-install: 123", "apt-get install -y x"), [(BLOCK_LINE + 1, INSTALLS)]),
        (("# runtime-install: -", "apt-get install -y x"), [(BLOCK_LINE + 1, INSTALLS)]),
        (("# runtime-installs: x", "apt-get install -y x"), [(BLOCK_LINE + 1, INSTALLS)]),
        (("# pytest-workers: x", "apt-get install -y x"), [(BLOCK_LINE + 1, INSTALLS)]),
        ((MARKER, "uvx x"), []),
        ((MARKER, "uv pip install --resolution lowest-direct -e ."), []),
        (("# pytest-workers: x", "uvx x"), [(BLOCK_LINE + 1, INSTALLS)]),
    ],
    ids=[
        "above", "on its line", "above two calls", "above a continued command", "on the last line of a continued command",
        "above a later line", "above a plain comment", "above a later line and a comment", "on the first call's line only", "on the line above a second call", "short reason",
        "wide spaces", "a letter of any script", "no colon", "no reason", "no letter", "dash only", "another id",
        "the other rule's id", "above a uvx call", "above a floor proof", "the other rule's id above a uvx call",
    ],
)
def test_a_marker_on_a_calls_line_or_directly_above_clears_that_call(build_repo, script, expected):
    """RUNTIME-7, RUNTIME-8, RUNTIME-13."""
    assert [(f.line, f.event) for f in judged(build_repo, block_run(*script))] == expected


def marked_step(*lines: str) -> str:
    return steps_of() + "".join(f"      {line}\n" for line in lines)


CALL = "apt-get install"
STEP_MARKERS = [
    pytest.param(
        marked_step(MARKER, "# a plain comment", "- run: apt-get install -y x"), [QUOTED_LINE + 2],
        id="a comment between the marker and a one-line step",
    ),
    pytest.param(
        marked_step("- name: t", f"  run: | {MARKER}", "    apt-get install -y x", "    apt-get install -y y"), [BLOCK_LINE + 1],
        id="on the run line of a block, covering its first line only",
    ),
    pytest.param(marked_step(f"- {MARKER}", "  run: apt-get install -y x"), [], id="on the dash line, clearing the plain run below"),
    pytest.param(
        marked_step("- name: t", f"  {MARKER}", "  run: |", "    apt-get install -y x"), [BLOCK_LINE + 1],
        id="between the name and the run",
    ),
    pytest.param(marked_step(f"- run: apt-get install -y x  {MARKER}"), [], id="on the line of a one-line step"),
    pytest.param(
        marked_step(MARKER, "- name: t", "  run: |", "    apt-get install -y x", "- name: u", "  run: |", "    apt-get install -y y"),
        [14],
        id="above the first of two steps, clearing that step only",
    ),
    pytest.param(
        marked_step(MARKER, "- name: t", "  run: |", "    apt-get update", "    apt-get install -y x"), [], id="dash column, a block of two calls",
    ),
    pytest.param(marked_step(MARKER, "- name: t", "  run: apt-get install -y x"), [], id="dash column, a plain value behind a name"),
    pytest.param(marked_step(MARKER, "- run: apt-get install -y x"), [], id="dash column, a plain value on the step line"),
    pytest.param(
        marked_step("- run: 'true'", MARKER, "- name: t", "  run: |", "    apt-get install -y x"), [], id="dash column, after the previous step",
    ),
    pytest.param(
        marked_step("- name: a", "  run: |", "    echo a", MARKER, "- name: t", "  run: curl u | sh"), [],
        id="dash column, after a block scalar",
    ),
    pytest.param(
        marked_step("- name: a", "  run: |", "    echo a", f"    {MARKER}", "- name: t", "  run: |", "    apt-get install -y x"), [14],
        id="a deeper column",
    ),
    pytest.param(
        marked_step("- name: a", "  run: |", "    echo a", f"  {MARKER}", "- name: t", "  run: |", "    apt-get install -y x"), [14],
        id="the run column",
    ),
    pytest.param(marked_step(MARKER, "-", "  name: t", "  run: apt-get install -y x"), [11], id="a dash that stands alone"),
    pytest.param(
        marked_step("- name: a", "  run: |", "    echo a", f"    {MARKER}", "- run: apt-get install -y x"), [12],
        id="a one-line step under a deeper column",
    ),
    pytest.param(
        marked_step("- name: a", "  run: |", "    echo a", f"  {MARKER}", "- run: 'apt-get install -y x'"), [12],
        id="a quoted one-line step under the run column",
    ),
    pytest.param(marked_step(f"- run: echo a  {MARKER}", "- run: apt-get install -y x"), [9], id="a marker on the previous one-line step"),
    pytest.param(
        marked_step("- name: a", "  run: |", f"    echo a  {MARKER}", "- run: apt-get install -y x"), [11],
        id="a marker trailing the last line of the previous block",
    ),
]


@pytest.mark.parametrize(("text", "lines"), STEP_MARKERS)
def test_a_marker_clears_the_calls_of_the_step_it_stands_beside_or_above_and_no_other(build_repo, text, lines):
    """RUNTIME-7: the step form wants the marker directly above the `-` or on its line; no other position clears a call."""
    assert event_line_fact(judged(build_repo, text)) == [(INSTALLS, line, CALL) for line in lines]


@pytest.mark.parametrize(
    ("above", "steps", "line"),
    [
        (f"    runs-on: x\n    {MARKER}\n", 'steps: [{name: t,\n      run: "apt-get install x"}]', 7),
        (f"    runs-on: x\n    {MARKER}\n", 'steps: [{run: "apt-get install x"}]', 6),
        (f"    runs-on: x  {MARKER}\n", 'steps: [{run: "apt-get install x"}]', 5),
        ("    runs-on: x\n", f'steps: [\n      {MARKER}\n      {{run: "apt-get install x"}}\n    ]', 7),
    ],
    ids=["over two lines", "on one line", "a marker trailing the line above", "a brace that starts its own line"],
)
def test_a_flow_step_has_no_step_form(build_repo, above, steps, line):
    """RUNTIME-7."""
    text = f"on: push\njobs:\n  j:\n{above}    {steps}\n"
    assert event_line_fact(judged(build_repo, text)) == [(INSTALLS, line, "apt-get install")]


def test_the_ids_of_the_two_markers_are_not_interchangeable(build_repo):
    """RUNTIME-7: a `runtime-install` marker clears no pytest call, and a `pytest-workers` marker above a step clears no install."""
    repo = build_repo({WORKFLOW: block_run("# runtime-install: reason", "pytest")})
    assert [finding.event for finding in rule_findings(repo, Rule.PYTEST_WORKERS)] == [FindingEvent.SERIAL]
    above_step = steps_of() + "      # pytest-workers: reason\n      - name: t\n        run: |\n          pytest\n"
    repo = build_repo({WORKFLOW: above_step})
    assert [finding.event for finding in rule_findings(repo, Rule.PYTEST_WORKERS)] == [FindingEvent.SERIAL]


