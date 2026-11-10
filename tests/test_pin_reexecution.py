"""`quality check` where the gate pins another commit: the release check before uv, uv's preparation, and the process
that becomes the prepared release's quality, run as processes against a fake uv and a local tag repository."""

import io
import json
import os
import select
import shlex
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path

import pytest

from quality import output, pin, process
from quality.report import CannotJudge, Release
from tests.support.door import LAUNCH_DRIVER, door_environment
from tests.support.git_repositories import git, head_commit, make_repo
from tests.support.layout import FILES_RULE_IDS

pytestmark = pytest.mark.integration

RELEASE = "v0.10.1"
QUALITY_URL = "https://github.com/overnightworks/quality"
TERMINATION_SECONDS = 10
PINNED_IN = "pinned in .github/workflows/ci.yml"
OFFLINE_PAIR = [
    f"error: cannot fetch quality {RELEASE}, which the gate pins · github.com did not answer", "next: quality check, once online",
]

FAKE_UV = """#!{python}
import json, os, subprocess, sys, time
from pathlib import Path
scenario = json.loads(Path({scenario!r}).read_text())
records = Path(scenario["records"])
held = len(list(records.iterdir())) if records.is_dir() else 0
with open(scenario["log"], "a") as log:
    log.write(json.dumps({{"argv": sys.argv[1:], "records": held, "repository": [name for name in ("GIT_INDEX_FILE", "GIT_DIR") if name in os.environ]}}) + "\\n")
answer = scenario["offline" if "--offline" in sys.argv else "online"]
if answer.get("block"):
    Path(scenario["uv_group"]).write_text(str(os.getpid()))
    holder = os.open(scenario["held"], os.O_WRONLY)
    subprocess.Popen([sys.executable, "-c", "import time; time.sleep(3600)"], pass_fds=(holder,))
    Path(scenario["ready"]).write_text("ready")
    time.sleep(3600)
if answer.get("print"):
    print(answer["print"])
sys.exit(answer.get("exit", 0))
"""

RECORDING_QUALITY = """#!{python}
import json, os, signal, subprocess, sys
from pathlib import Path
def is_open(descriptor):
    try:
        os.fstat(descriptor)
    except OSError:
        return False
    return True
staged = subprocess.run(["git", "diff", "--cached", "--name-only"], capture_output=True, text=True).stdout.split()
Path({record!r}).write_text(json.dumps({{
    "argv": sys.argv[1:], "cwd": os.getcwd(), "guard": os.environ.get({guard!r}), "foo": os.environ.get("FOO"),
    "tty": os.isatty(0), "out_open": is_open(1), "err_open": is_open(2), "pid": os.getpid(), "ppid": os.getppid(),
    "index": os.environ.get("GIT_INDEX_FILE"), "staged": staged,
}}))
exit_as = os.environ.get("FAKE_EXIT", "0")
if exit_as == "SIGINT":
    os.kill(os.getpid(), signal.SIGINT)
sys.exit(int(exit_as))
"""

COMPOSED_QUALITY = """#!{python}
import os, sys
os.execv(sys.executable, [sys.executable, "-c", {driver!r}, *{words!r}, *sys.argv[1:]])
"""


@dataclass
class Scene:
    """A judged repository pinned `<P> # v0.10.1`, the tag repository git puts in place of quality's remote, the fake
    uv first on PATH and the prepared environment it names."""

    root: Path
    tags: Path
    commit: str
    judged: Path

    @property
    def records(self) -> Path:
        return pin.release_records({"XDG_CACHE_HOME": str(self.root / "cache")})

    def scripts(self) -> Path:
        return self.root / "env" / "bin"

    def answer(self, offline: dict | None = None, online: dict | None = None) -> None:
        valid = {"print": str(self.scripts())}
        scenario = {
            "records": str(self.records), "log": str(self.root / "uv.log"), "held": str(self.root / "held"), "uv_group": str(self.root / "uv_group"), "ready": str(self.root / "ready"),
            "offline": valid if offline is None else offline, "online": valid if online is None else online,
        }
        (self.root / "scenario.json").write_text(json.dumps(scenario))

    def recording_child(self) -> None:
        self._write_quality(RECORDING_QUALITY.format(python=sys.executable, record=str(self.root / "child.json"), guard=pin.RUN_AT_PIN_VARIABLE))

    def composed_child(self, *words: str) -> None:
        self._write_quality(COMPOSED_QUALITY.format(python=sys.executable, driver=LAUNCH_DRIVER, words=list(words)))

    def _write_quality(self, text: str) -> None:
        quality = self.scripts() / "quality"
        quality.write_text(text)
        quality.chmod(0o755)

    def child(self) -> dict:
        return json.loads((self.root / "child.json").read_text())

    def uv_calls(self) -> list[dict]:
        log = self.root / "uv.log"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def queried_tags(self) -> bool:
        trace = self.root / "trace"
        return trace.exists() and "ls-remote" in trace.read_text()

    def environment(self, **extra: str) -> dict[str, str]:
        kept = {key: value for key, value in door_environment().items() if key != pin.RUN_AT_PIN_VARIABLE}
        return kept | {
            "PATH": f"{self.root / 'bin'}:{os.environ['PATH']}", "XDG_CACHE_HOME": str(self.root / "cache"), "GIT_TRACE": str(self.root / "trace"),
            "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": f"url.{self.tags}.insteadOf", "GIT_CONFIG_VALUE_0": QUALITY_URL,
        } | extra

    def parent(self, *words: str, running: str | None = None) -> list[str]:
        return [sys.executable, "-c", LAUNCH_DRIVER, "--running", running or f"0.10.0:vcs:{'4d' * 20}", *words]

    def run(self, *words: str, running: str | None = None, cwd: Path | None = None, wrap: tuple[str, ...] = (), **extra: str):
        return subprocess.run(
            [*wrap, *self.parent(*words, running=running)], cwd=cwd or self.judged, env=self.environment(**extra), capture_output=True, text=True,
            check=False, timeout=120,
        )


def new_repo(directory: Path, files: dict[str, str]) -> Path:
    directory.mkdir()
    return make_repo(directory, files)


def gate_ci(commit: str, release: str = RELEASE) -> dict[str, str]:
    uses = f"overnightworks/quality/.github/workflows/gate.yml@{commit} # {release}"
    return {".github/workflows/ci.yml": f"on: push\njobs:\n  gate:\n    uses: {uses}\n"}


@pytest.fixture
def scene(tmp_path) -> Scene:
    tags = new_repo(tmp_path / "tags", {"src/quality/__init__.py": "VERSION = 1\n"})
    git(tags, "tag", RELEASE)
    commit = head_commit(tags)
    judged = new_repo(tmp_path / "shop", {**gate_ci(commit), "README.md": "shop\n"})
    built = Scene(tmp_path, tags, commit, judged)
    (tmp_path / "bin").mkdir()
    fake_uv = tmp_path / "bin" / "uv"
    fake_uv.write_text(FAKE_UV.format(python=sys.executable, scenario=str(tmp_path / "scenario.json")))
    fake_uv.chmod(0o755)
    built.scripts().mkdir(parents=True)
    built.recording_child()
    built.answer()
    return built


def release_row(fact: str) -> str:
    return f"  · release       {RELEASE}        {fact}"


def test_the_child_judges_at_the_pin_and_names_its_fetch(scene):
    """PIN-5, PIN-6, PIN-13, PIN-17, PIN-22, PIN-25, PIN-26: a run that re-executes judges as the pinned release; the
    first time it fetches the release and names the fetch in its row, the second prepares offline from the record the
    first kept and names the cached release."""
    scene.composed_child("--running", f"0.10.1:vcs:{scene.commit}")
    fetched = scene.run("check", "files")
    cached = scene.run("check", "files")
    assert (fetched.returncode, fetched.stdout.splitlines()[:2], fetched.stderr) == (
        0, [release_row(f"fetched now · {PINNED_IN}"), f"  ✓ files         clean          {len(FILES_RULE_IDS)} rules · 2 files"], "",
    )
    assert (cached.returncode, cached.stdout.splitlines()[0], [("--offline" in call["argv"]) for call in scene.uv_calls()]) == (
        0, release_row(PINNED_IN), [False, True],
    )


@pytest.mark.parametrize(
    ("running", "error", "step"),
    [
        pytest.param(
            f"0.10.1:vcs:{'4d' * 20}", f"error: the gate pins {{short}} as {RELEASE}; uv prepared quality 0.10.1 at 4d4d4d4",
            "uv cache clean overnightworks-quality", id="another commit",
        ),
        pytest.param(
            "0.10.2:vcs:{commit}", f"error: the gate pins {{short}} as {RELEASE}, but that commit is quality 0.10.2",
            "make the pin's comment name its commit's own tag, as docs/gate.md shows", id="another release",
        ),
    ],
)
def test_a_child_that_is_not_the_pin_refuses(scene, running, error, step):
    """PIN-10, PIN-18: a re-executed run that is not the pin refuses with the pair, whether its commit or its release
    differs."""
    scene.composed_child("--running", running.format(commit=scene.commit))
    done = scene.run("check", "files")
    assert (done.returncode, done.stdout, done.stderr.splitlines()[0], step in done.stderr) == (2, "", error.format(short=scene.commit[:7]), True)


def test_the_child_gets_the_same_words_folder_environment_and_terminal(scene):
    """PIN-15, PIN-17: the re-executed run gets the words, the folder, the environment and the terminal it was started
    with, and the mark."""
    folder = scene.judged / "sub"
    folder.mkdir()
    words = ("check", "debt", "--fix", "--dry-run", "-v")
    terminal = ["script", "-qec", shlex.join(scene.parent(*words)), "/dev/null"]
    done = subprocess.run(terminal, cwd=folder, env=scene.environment(FOO="bar"), capture_output=True, text=True, check=False, timeout=120)
    child = scene.child()
    assert (done.returncode, child["argv"], child["cwd"], child["foo"], child["guard"], child["tty"]) == (
        0, list(words), str(folder), "bar", pin.Fetch.FETCHED, True,
    )


def test_the_commit_s_own_index_reaches_the_child_and_never_uv(scene):
    """PIN-15: the commit hook's own index reaches the re-executed run."""
    for name in ("a.txt", "b.txt"):
        (scene.judged / name).write_text(f"{name}\n")
    git(scene.judged, "add", "a.txt", "b.txt")
    hook = scene.judged / ".git" / "hooks" / "pre-commit"
    hook.write_text(f"#!/bin/sh\nexec {shlex.join(scene.parent('check', 'secrets', '--staged'))}\n")
    hook.chmod(0o755)
    committed = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", f"core.hooksPath={hook.parent}", "commit", "-q", "-m", "x", "--", "a.txt"],
        cwd=scene.judged,
        env=scene.environment(), capture_output=True, text=True, check=False,
    )
    child = scene.child()
    assert (committed.returncode, child["staged"], child["index"] is not None, [call["repository"] for call in scene.uv_calls()]) == (
        0, ["a.txt"], True, [[]],
    )


def test_the_child_is_the_process(scene):
    """PIN-14: the re-executed run is the process that was started: its pid and its parent are the same."""
    with subprocess.Popen(scene.parent("check", "files"), cwd=scene.judged, env=scene.environment(), stdout=subprocess.PIPE, stderr=subprocess.PIPE) as started:
        try:
            started.communicate(timeout=120)
        finally:
            started.terminate()
            try:
                started.wait(timeout=TERMINATION_SECONDS)
            except subprocess.TimeoutExpired:
                started.kill()
    child = scene.child()
    assert (child["pid"], child["ppid"]) == (started.pid, os.getpid())


@pytest.mark.parametrize(("exit_as", "expected"), [("0", 0), ("1", 1), ("2", 2), ("SIGINT", -signal.SIGINT)])
def test_the_exit_is_the_childs(scene, exit_as, expected):
    """PIN-16: the exit of the re-executed run, a code or a signal, is the exit."""
    assert scene.run("check", "files", FAKE_EXIT=exit_as).returncode == expected


@pytest.mark.parametrize(
    ("closing", "expected"), [pytest.param(">&-", (False, True), id="stdout closed"), pytest.param("2>&-", (True, False), id="stderr closed")]
)
def test_a_closed_stream_passes_to_the_child(scene, closing, expected):
    """PIN-15: a closed standard stream stays closed in the re-executed run, with no traceback."""
    done = scene.run("check", "files", wrap=("sh", "-c", f'exec "$@" {closing}', "_"), FAKE_EXIT="1")
    child = scene.child()
    assert (done.returncode, (child["out_open"], child["err_open"]), "Traceback" in done.stdout + done.stderr) == (1, expected, False)


def keep_another_release(scene: Scene) -> None:
    pin.keep_record(scene.records, scene.commit, "v0.10.0")


@pytest.mark.parametrize(
    ("arrange", "calls", "queried", "guard"),
    [
        pytest.param(lambda scene: None, [(False, 1)], True, pin.Fetch.FETCHED, id="no record, online"),
        pytest.param(lambda scene: pin.keep_record(scene.records, scene.commit, RELEASE), [(True, 1)], False, pin.Fetch.CACHED, id="a record, cached"),
        pytest.param(
            lambda scene: (pin.keep_record(scene.records, scene.commit, RELEASE), scene.answer(offline={"exit": 1})), [(True, 1), (False, 1)], True,
            pin.Fetch.FETCHED, id="a record, its environment gone",
        ),
        pytest.param(keep_another_release, [(False, 2)], True, pin.Fetch.FETCHED, id="a record of another release"),
    ],
)
def test_the_release_check_comes_before_any_uv_call(scene, arrange, calls, queried, guard):
    """PIN-17, PIN-21, PIN-22, PIN-25, PIN-26: quality's remote is asked where no record of the commit and release
    stands, the answer leaves the record, the mark names whether the release was fetched, and uv prepares offline only
    where a record of this release stands."""
    arrange(scene)
    done = scene.run("check", "files")
    seen = [("--offline" in call["argv"], call["records"]) for call in scene.uv_calls()]
    assert (done.returncode, seen, scene.queried_tags(), scene.child()["guard"], pin.is_recorded(scene.records, scene.commit, RELEASE)) == (
        0, calls, queried, guard, True,
    )


@pytest.mark.parametrize("cached", [pytest.param(False, id="nothing cached"), pytest.param(True, id="uv's cache warm, no record")])
def test_an_unreachable_github_is_the_offline_pair(scene, cached):
    """PIN-27, PIN-28: with no answer from github.com, a release not yet checked on quality's remote is the offline pair
    with no uv call, even where uv's cache holds it."""
    scene.answer(offline={"print": str(scene.scripts())} if cached else {"exit": 1})
    done = scene.run("check", "files", GIT_CONFIG_KEY_0=f"url.{scene.root / 'absent'}.insteadOf")
    assert (done.returncode, done.stdout, done.stderr.splitlines(), scene.uv_calls()) == (2, "", OFFLINE_PAIR, [])


def test_a_comment_that_misnames_its_commit_runs_nothing(scene):
    """PIN-10, PIN-20, PIN-22: a comment that misnames its commit on quality's remote is the pair, with no uv call and
    no record."""
    git(scene.tags, "commit", "-q", "--allow-empty", "-m", "later")
    git(scene.tags, "tag", "-f", RELEASE)
    other = head_commit(scene.tags)
    done = scene.run("check", "files")
    error = f"error: the gate pins {scene.commit[:7]} as {RELEASE}, but quality's {RELEASE} is {other[:7]}"
    assert (done.returncode, done.stderr.splitlines()[0], scene.uv_calls(), pin.is_recorded(scene.records, scene.commit, RELEASE)) == (
        2, error, [], False,
    )


def test_an_unwritable_record_directory_still_judges(scene):
    """PIN-13: a record directory that refuses the record leaves the run judging as the pinned release."""
    (scene.root / "cache").mkdir(mode=0o500)
    first = scene.run("check", "files")
    guard = scene.child()["guard"]
    second = scene.run("check", "files")
    assert (first.returncode, guard, second.returncode, [call["records"] for call in scene.uv_calls()], scene.queried_tags()) == (
        0, pin.Fetch.FETCHED, 0, [0, 0], True,
    )


def test_an_editable_installation_judges_at_the_pin_only_while_clean(scene):
    """PIN-1, PIN-4, PIN-5, PIN-13, PIN-17: an editable installation judges at the pin while its work tree is clean and
    re-executes once a tracked file changed."""
    tool = scene.root / "tool"
    git(scene.tags, "worktree", "add", "-q", "--detach", str(tool), RELEASE)
    clean = scene.run("check", "files", running=f"0.10.1:dir:{tool}")
    clean_calls = scene.uv_calls()
    with (tool / "src" / "quality" / "__init__.py").open("a") as source:
        source.write("CHANGED = True\n")
    changed = scene.run("check", "files", running=f"0.10.1:dir:{tool}")
    assert (clean.returncode, clean.stdout.splitlines()[0], clean_calls, changed.returncode, scene.child()["guard"]) == (
        0, release_row(PINNED_IN), [], 0, pin.Fetch.FETCHED,
    )


def test_uv_missing_is_the_pair(scene):
    """PIN-30: a uv that is not installed is the pair that asks to install it."""
    bin_directory = scene.root / "uv-free-bin"
    bin_directory.mkdir()
    for tool in ("git", "sh"):
        (bin_directory / tool).symlink_to(shutil.which(tool))
    done = scene.run("check", "files", PATH=str(bin_directory))
    assert (done.returncode, done.stderr.splitlines()) == (
        2, [f"error: cannot fetch quality {RELEASE}, which the gate pins · uv is not installed", "next: install uv, then run quality check again"],
    )


def test_a_vanished_environment_is_the_pair(scene):
    """PIN-32: a prepared quality that cannot start is the pair that names the cause and asks to run again."""
    scene._write_quality("#!/vanished/python\n")
    done = scene.run("check", "files")
    assert (done.returncode, done.stderr.splitlines()) == (
        2, [f"error: cannot start quality {RELEASE} · No such file or directory", "next: quality check again"],
    )


def wait_for_group_to_end(started: subprocess.Popen) -> None:
    """Returns once no process of `started`'s group is left (reaping `started` itself), which is when quality, a member of it, has unwound its cleanup, or after `TERMINATION_SECONDS`."""
    deadline = time.monotonic() + TERMINATION_SECONDS
    while time.monotonic() < deadline:
        started.poll()
        try:
            os.killpg(started.pid, 0)
        except OSError:
            return
        time.sleep(0.05)


@contextmanager
def launched(scene: Scene, command: list[str]) -> Iterator[subprocess.Popen]:
    """`command` started once its readiness FIFO exists, yielded after it wrote `ready`; on the way out its group is
    asked to end, so quality unwinds and kills the preparation groups it started, and what is left after
    `TERMINATION_SECONDS`, uv's group included, is killed."""
    os.mkfifo(scene.root / "ready")
    ready = os.open(scene.root / "ready", os.O_RDONLY | os.O_NONBLOCK)
    try:
        with subprocess.Popen(
            command, cwd=scene.judged, env=scene.environment(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True
        ) as started:
            try:
                readable, _, _ = select.select([ready], [], [], 60)
                assert readable
                assert os.read(ready, 10) == b"ready"
                yield started
            finally:
                with suppress(OSError):
                    os.killpg(started.pid, signal.SIGTERM)
                wait_for_group_to_end(started)
                with suppress(OSError, ValueError):
                    os.killpg(int((scene.root / "uv_group").read_text()), signal.SIGKILL)
                with suppress(OSError):
                    os.killpg(started.pid, signal.SIGKILL)
    finally:
        os.close(ready)


@contextmanager
def watching_descendants(scene: Scene) -> Iterator[int]:
    """A descriptor that reads end of file once no process holds the `held` FIFO's write end."""
    os.mkfifo(scene.root / "held")
    held = os.open(scene.root / "held", os.O_RDONLY | os.O_NONBLOCK)
    try:
        yield held
    finally:
        os.close(held)


@pytest.mark.parametrize(
    ("stop", "ends_as"), [pytest.param(signal.SIGINT, -signal.SIGINT, id="Ctrl-C"), pytest.param(signal.SIGTERM, 128 + signal.SIGTERM, id="SIGTERM")]
)
def test_a_stop_during_preparation_ends_the_run_and_its_processes(scene, stop, ends_as):
    """PIN-33, CLOSING-31, CLOSING-32: a stop during the preparation ends quality and every process uv started, and
    Ctrl-C ends it by SIGINT with no traceback."""
    scene.answer(online={"block": True})
    with watching_descendants(scene) as held, launched(scene, scene.parent("check", "files")) as started:
        started.send_signal(stop)
        _, stderr = started.communicate(timeout=60)
        readable, _, _ = select.select([held], [], [], 60)
        assert (started.returncode, bool(readable) and os.read(held, 10), "Traceback" in stderr) == (ends_as, b"", False)


def commit_python_file(scene: Scene) -> None:
    (scene.judged / "main.py").write_text("print(1)\n")
    git(scene.judged, "add", "main.py")
    git(scene.judged, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "python")


def test_an_interrupt_after_the_reexecution_ends_as_the_child_s(scene):
    """CLOSING-31, CLOSING-32: an interrupt after the re-execution ends the run by SIGINT with no traceback."""
    scene.composed_child("--running", f"0.10.1:vcs:{scene.commit}", "--block-ruff", str(scene.root / "ready"))
    commit_python_file(scene)
    with launched(scene, scene.parent("check", "code")) as started:
        started.send_signal(signal.SIGINT)
        _, stderr = started.communicate(timeout=60)
        assert (started.returncode, "Traceback" in stderr) == (-signal.SIGINT, False)


@pytest.mark.parametrize("moment", ["during preparation", "after the reexecution"])
def test_a_calling_shell_loop_stops(scene, moment):
    """PIN-33, CLOSING-31: a Ctrl-C that reaches a calling shell loop ends quality by SIGINT, so the loop stops, and
    during the preparation it ends every process uv started."""
    if moment == "during preparation":
        scene.answer(online={"block": True})
        words = ("check", "files")
    else:
        scene.composed_child("--running", f"0.10.1:vcs:{scene.commit}", "--block-ruff", str(scene.root / "ready"))
        commit_python_file(scene)
        words = ("check", "code")
    loop = ["bash", "-c", 'for i in 1 2; do "$@"; echo next; done', "_", *scene.parent(*words)]
    with watching_descendants(scene) as held, launched(scene, loop) as started:
        os.killpg(started.pid, signal.SIGINT)
        stdout, _ = started.communicate(timeout=60)
        assert (started.returncode, "next" in stdout) == (-signal.SIGINT, False)
        if moment == "during preparation":
            readable, _, _ = select.select([held], [], [], 60)
            assert readable
            assert os.read(held, 10) == b""


def own_gate_ci() -> dict[str, str]:
    return {".github/workflows/ci.yml": "on: push\njobs:\n  gate:\n    uses: ./.github/workflows/gate.yml\n"}


def test_quality_s_checkout_reexecutes_through_uv_run(scene):
    """PIN-34: quality's checkout is prepared through `uv run`, and an installation run there re-executes into the
    checkout's code."""
    checkout = new_repo(scene.root / "quality", own_gate_ci())
    git(checkout, "remote", "add", "origin", QUALITY_URL)
    done = scene.run("check", "files", cwd=checkout)
    calls = [call["argv"][:6] for call in scene.uv_calls()]
    assert (done.returncode, calls, scene.queried_tags(), scene.child()["guard"]) == (
        0, [["run", "--offline", "--quiet", "--locked", "--project", str(checkout)]], False, pin.Fetch.CACHED,
    )


def test_an_unrelated_checkout_never_runs_its_code(scene):
    """PIN-8: a repository that is not quality's and calls its own gate.yml pins no gate, so the run refuses and
    prepares nothing."""
    unrelated = new_repo(scene.root / "acme", own_gate_ci())
    git(unrelated, "remote", "add", "origin", "https://github.com/acme/shop")
    done = scene.run("check", "files", cwd=unrelated)
    assert (done.returncode, done.stderr.splitlines()[0], scene.uv_calls()) == (
        2, "error: no workflow here calls quality's gate.yml, so no release is pinned", [],
    )


def test_the_live_line_shows_its_fact():
    """COLOUR-13: the line redraws with a fact after its event column and is erased at the end."""
    stream = io.StringIO()
    with output.live_line(output.Channel(stream, False, 60))("release", "fetching") as show:
        show("v0.10.1 · 4s")
    assert stream.getvalue() == (
        "  ⠋ release       fetching\r" "\r\x1b[K  ⠋ release       fetching       v0.10.1 · 4s\r" "\r\x1b[K"
    )


def test_a_stream_without_a_window_shows_no_fact():
    """COLOUR-7: a stream with no window shows no fact."""
    stream = io.StringIO()
    with output.live_line(output.Channel(stream, False, None))("release", "fetching") as show:
        show("v0.10.1 · 4s")
    assert stream.getvalue() == ""


def leads_to_the_null_device(descriptor: int) -> bool:
    return os.fstat(descriptor).st_rdev == os.stat(os.devnull).st_rdev


def _missing(_tmp_path: Path):
    return None, lambda: True


def _closed_object(_tmp_path: Path):
    stream = io.TextIOWrapper(io.BytesIO())
    stream.close()
    return stream, lambda: True


def _reader_that_left(_tmp_path: Path):
    read_end, write_end = os.pipe()
    os.close(read_end)
    stream = open(write_end, "w")  # noqa: SIM115 - the test owns the stream past this function
    stream.write("a line\n")
    return stream, lambda: leads_to_the_null_device(write_end)


def _full_disk(_tmp_path: Path):
    stream = open("/dev/full", "w")  # noqa: SIM115 - the test owns the stream past this function
    stream.write("a line\n")
    return stream, lambda: leads_to_the_null_device(stream.fileno())


def _open_pipe(_tmp_path: Path):
    read_end, write_end = os.pipe()
    stream = open(write_end, "w")  # noqa: SIM115 - the test owns the stream past this function
    stream.write("a line\n")
    return stream, lambda: os.read(read_end, 100) == b"a line\n"


@pytest.mark.parametrize(
    "arrange",
    [
        pytest.param(_missing, id="missing"),
        pytest.param(_closed_object, id="a closed object"),
        pytest.param(_reader_that_left, id="a reader that left"),
        pytest.param(_full_disk, id="a full disk"),
        pytest.param(_open_pipe, id="a stream that takes the line"),
    ],
)
def test_the_streams_flush_before_the_handover(tmp_path, monkeypatch, arrange):
    stream, holds = arrange(tmp_path)
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setattr(sys, "stderr", None)
    output.flush_streams()
    assert holds()


def test_the_live_line_counts_on_through_both_preparation_steps(tmp_path, monkeypatch):
    """COLOUR-13: the line counts the seconds on through the release check and uv's preparation, behind the tag."""
    shown = []
    commit = "5e" * 20

    @contextmanager
    def live(_name, _fact):
        yield shown.append

    def reports_seconds(command, _timeout, _environment, cwd=None, every_second=None):
        if every_second is not None:
            every_second(1)
        if command[0] != "git":
            raise process.ProcessError(f"{command[0]} did not answer")
        every_second(2)
        return subprocess.CompletedProcess(command, 0, f"{commit}\trefs/tags/{RELEASE}\n", "")

    monkeypatch.setattr(process, "run_bounded_in_own_group", reports_seconds)
    pinned = pin.ReleaseAtPin(Release(RELEASE), commit)
    with pytest.raises(CannotJudge):
        pin.reexecute(pinned, "quality check", ["check"], {"XDG_CACHE_HOME": str(tmp_path)}, live)
    assert shown == [f"{RELEASE} · {seconds}s" for seconds in (0, 1, 2, 3)]
