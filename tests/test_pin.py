from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from quality import checkout, pin, process
from quality.installation import DirectoryOrigin, VcsOrigin
from quality.pin import JudgingRelease, ReleaseAtPin, ReleaseEvent, RunningQuality, ThisCheckout
from quality.report import CannotJudge, Command, Instruction, Release
from tests.support.git_repositories import git, head_commit, make_repo

CHECK = "quality check"


def pinned_commit() -> str:
    return "5e" * 20


def other_commit() -> str:
    return "4d" * 20


def dev_version() -> str:
    return "0.10.1.dev4+g8c8121b"


@dataclass(frozen=True)
class Scene:
    """The judged repository, and the source repository a directory installation was built from with its two commits:
    `tagged`, which carries the tag `v0.10.0`, and `later`, one commit after it."""

    judged: Path
    tool: Path
    tagged: str
    later: str


def gate_call(commit: str, release: str) -> dict[str, str]:
    uses = f"{checkout.GATE_REPOSITORY}/.github/workflows/gate.yml@{commit} # {release}"
    return {".github/workflows/ci.yml": f"on: push\njobs:\n  gate:\n    uses: {uses}\n"}


def own_gate() -> dict[str, str]:
    return {".github/workflows/ci.yml": "on: push\njobs:\n  gate:\n    uses: ./.github/workflows/gate.yml\n"}


def floor_parts() -> tuple[int, ...]:
    return checkout.release_order(pin.FIRST_RELEASE_WITH_CHECK)


def below_the_floor() -> str:
    major, minor, patch = floor_parts()
    return f"v{major}.{minor - 1}.{patch}"


def floor_version() -> str:
    return ".".join(map(str, floor_parts()))


def vcs(version: str, commit: str) -> Callable[[Scene], RunningQuality]:
    return lambda scene: RunningQuality(version, Path(pin.__file__).parent, VcsOrigin(commit))


def from_tool(version: str = "0.10.0") -> Callable[[Scene], RunningQuality]:
    return lambda scene: RunningQuality(version, scene.tool / "src" / "quality", DirectoryOrigin(scene.tool))


def from_checkout(scene: Scene) -> RunningQuality:
    return RunningQuality(dev_version(), scene.judged / "src" / "quality", DirectoryOrigin(scene.judged))


def built_elsewhere(scene: Scene) -> RunningQuality:
    return RunningQuality("0.10.0", scene.tool.parent / "site-packages" / "quality", DirectoryOrigin(scene.tool))


def reexecutes_at(pinned: Callable[[Scene], str]) -> Callable[[Scene], ReleaseAtPin]:
    return lambda scene: ReleaseAtPin(Release("v0.10.0"), pinned(scene))


def at_tagged(scene: Scene) -> str:
    return scene.tagged


def at_later(scene: Scene) -> str:
    return scene.later


def change_tracked_source(scene: Scene, _monkeypatch: pytest.MonkeyPatch) -> None:
    with (scene.tool / "src" / "quality" / "__init__.py").open("a") as source:
        source.write("CHANGED = True\n")


def add_untracked_file(scene: Scene, _monkeypatch: pytest.MonkeyPatch) -> None:
    (scene.tool / "src" / "quality" / "extra.py").write_text("EXTRA = True\n")


def check_out_later(scene: Scene, _monkeypatch: pytest.MonkeyPatch) -> None:
    git(scene.tool, "checkout", "-q", scene.later)


def tag_the_dev_version(scene: Scene, _monkeypatch: pytest.MonkeyPatch) -> None:
    git(scene.tool, "tag", f"v{dev_version()}", scene.tagged)


def inside_a_commit_hook(scene: Scene, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_DIR", str(scene.judged / ".git"))
    monkeypatch.setenv("GIT_INDEX_FILE", str(scene.judged / ".git" / "index"))


def nothing(_scene: Scene, _monkeypatch: pytest.MonkeyPatch) -> None:
    return None


def pinned_at(commit: Callable[[Scene], str], release: str = "v0.10.0") -> Callable[[Scene], dict[str, str]]:
    return lambda scene: gate_call(commit(scene), release)


def fixed(files: dict[str, str]) -> Callable[[Scene], dict[str, str]]:
    return lambda scene: files


def released(release: str) -> JudgingRelease:
    return JudgingRelease(Release(release), "pinned in .github/workflows/ci.yml")


CASES = [
    pytest.param(
        fixed({"README.md": "no gate\n"}), (), vcs("0.10.0", pinned_commit()), nothing, False,
        lambda scene: ("no workflow here calls quality's gate.yml, so no release is pinned", (Instruction("add the gate as quality's docs/gate.md describes"),)),
        id="no gate call",
    ),
    pytest.param(
        fixed({"README.md": "no gate\n"}), (), vcs("0.10.0", pinned_commit()), nothing, True,
        lambda scene: JudgingRelease(Release("v0.10.0"), "installed · no gate pins a release here"), id="the commit hook without a gate",
    ),
    pytest.param(
        fixed({"README.md": "no gate\n"}), (), vcs(dev_version(), pinned_commit()), nothing, True,
        lambda scene: JudgingRelease(Release(dev_version()), "installed · no gate pins a release here"), id="the commit hook without a gate at a dev version",
    ),
    pytest.param(
        fixed(gate_call(pinned_commit(), below_the_floor())), (), vcs("0.10.0", other_commit()), nothing, True,
        lambda scene: (
            f"the gate pins quality {below_the_floor()}, which has no quality check",
            (Instruction(f"raise the gate pin to {pin.FIRST_RELEASE_WITH_CHECK} or later"),),
        ),
        id="the commit hook with a pin below the floor",
    ),
    pytest.param(
        fixed(gate_call("main", "v0.10.0")), (), vcs("0.10.0", pinned_commit()), nothing, False,
        lambda scene: (
            "the gate pin in .github/workflows/ci.yml is not @<commit sha> # v<tag>", (Instruction("pin the gate as docs/gate.md shows: @<commit sha> # v<tag>"),),
        ),
        id="an unknown pin",
    ),
    pytest.param(
        fixed(own_gate()), (f"https://github.com/{checkout.GATE_REPOSITORY}",), from_checkout, nothing, False,
        lambda scene: JudgingRelease(ReleaseEvent.THIS_CHECKOUT, f"{dev_version()} · quality judges itself"), id="own gate, its own code",
    ),
    pytest.param(
        fixed(own_gate()), (f"https://github.com/{checkout.GATE_REPOSITORY}",), vcs("0.10.0", pinned_commit()), nothing, False,
        lambda scene: ThisCheckout(scene.judged), id="own gate, another installation",
    ),
    pytest.param(
        fixed(gate_call(pinned_commit(), below_the_floor())), (), vcs("0.10.0", other_commit()), nothing, False,
        lambda scene: (
            f"the gate pins quality {below_the_floor()}, which has no quality check",
            (Instruction(f"raise the gate pin to {pin.FIRST_RELEASE_WITH_CHECK} or later"),),
        ),
        id="a pin below the floor",
    ),
    pytest.param(
        fixed(gate_call(pinned_commit(), pin.FIRST_RELEASE_WITH_CHECK)), (), vcs(floor_version(), pinned_commit()), nothing, False,
        lambda scene: released(pin.FIRST_RELEASE_WITH_CHECK), id="the pin at the floor",
    ),
    pytest.param(
        fixed(gate_call(pinned_commit(), "v0.10.1")), (), vcs("0.10.0", other_commit()), nothing, False,
        lambda scene: ReleaseAtPin(Release("v0.10.1"), pinned_commit()), id="another release",
    ),
    pytest.param(
        fixed(gate_call(pinned_commit(), "v0.10.1")), (), vcs("0.10.1", pinned_commit().upper()), nothing, False, lambda scene: released("v0.10.1"), id="the running release",
    ),
    pytest.param(
        fixed(gate_call(pinned_commit(), "v0.10.0")), (), vcs("0.10.1", pinned_commit()), nothing, False,
        lambda scene: (
            "the gate pins 5e5e5e5 as v0.10.0, but that commit is quality 0.10.1",
            (Instruction("make the pin's comment name its commit's own tag, as docs/gate.md shows"),),
        ),
        id="the pinned commit, its comment another tag",
    ),
    pytest.param(
        fixed(gate_call(pinned_commit(), "v0.10.0")), (), vcs("0.10.0", other_commit()), nothing, False,
        lambda scene: ReleaseAtPin(Release("v0.10.0"), pinned_commit()), id="installed 0.10.0, the pin 0.10.1's commit commented v0.10.0",
    ),
    pytest.param(
        pinned_at(at_tagged), (), from_tool(), nothing, False, lambda scene: released("v0.10.0"), id="a clean directory installation at the pinned tag",
    ),
    pytest.param(
        pinned_at(at_later), (), from_tool(), check_out_later, False, reexecutes_at(at_later),
        id="a directory installation whose tag names another commit",
    ),
    pytest.param(
        pinned_at(at_tagged), (), from_tool(), inside_a_commit_hook, False, lambda scene: released("v0.10.0"),
        id="a directory installation inside a commit hook",
    ),
    pytest.param(
        pinned_at(at_tagged), (), from_tool(dev_version()), tag_the_dev_version, False, reexecutes_at(at_tagged),
        id="a directory installation at a dev version",
    ),
    pytest.param(
        pinned_at(at_tagged), (), from_tool(), change_tracked_source, False, reexecutes_at(at_tagged),
        id="a directory installation whose tracked source changed after its install",
    ),
    pytest.param(
        pinned_at(at_tagged), (), from_tool(), add_untracked_file, False, reexecutes_at(at_tagged), id="a directory installation with an untracked file",
    ),
    pytest.param(
        pinned_at(at_tagged), (), from_tool(), check_out_later, False, reexecutes_at(at_tagged),
        id="a directory installation checked out at another commit",
    ),
    pytest.param(
        pinned_at(at_tagged), (), built_elsewhere, nothing, False, reexecutes_at(at_tagged), id="a directory installation built into another place",
    ),
    pytest.param(
        pinned_at(lambda scene: pinned_commit()), (), lambda scene: RunningQuality("0.10.0", Path(pin.__file__).parent, None), nothing, False,
        reexecutes_at(lambda scene: pinned_commit()), id="no recorded origin",
    ),
    pytest.param(
        pinned_at(lambda scene: pinned_commit()), (), vcs("0.10.0", pinned_commit()[:7]), nothing, False, reexecutes_at(lambda scene: pinned_commit()),
        id="a recorded commit not in the full form",
    ),
]


@pytest.fixture
def tool(tmp_path_factory) -> tuple[Path, str, str]:
    """A source repository of quality's shape: its first commit tagged `v0.10.0` (annotated), then one more commit."""
    directory = make_repo(tmp_path_factory.mktemp("tool"), {"src/quality/__init__.py": "VERSION = 1\n"})
    tagged = head_commit(directory)
    git(directory, "tag", "-a", "v0.10.0", "-m", "release")
    git(directory, "commit", "-q", "--allow-empty", "-m", "later")
    later = head_commit(directory)
    git(directory, "checkout", "-q", tagged)
    return directory, tagged, later


@pytest.mark.integration
@pytest.mark.parametrize(("files", "remotes", "running", "arrange", "staged", "expected"), CASES)
def test_the_release_that_judges_here_is_decided_from_the_gate_pin(build_repo, tool, monkeypatch, files, remotes, running, arrange, staged, expected):
    """PIN-1, PIN-2, PIN-3, PIN-4, PIN-5, PIN-7, PIN-8, PIN-9, PIN-10, PIN-11, PIN-12: the gate pin, the running
    commit and the checkout decide which release judges, the release to re-execute into, or the refusal and its next
    step."""
    directory, tagged, later = tool
    placeholder = Scene(Path(), directory, tagged, later)
    judged = build_repo(files(placeholder))
    scene = Scene(judged, directory, tagged, later)
    for number, url in enumerate(remotes):
        git(judged, "remote", "add", f"remote{number}", url)
    arrange(scene, monkeypatch)
    wanted = expected(scene)
    running_here = running(scene)
    if not isinstance(wanted, tuple):
        assert pin.judging_release(judged, CHECK, staged, {}, running=running_here) == wanted
    else:
        with pytest.raises(CannotJudge) as refused:
            pin.judging_release(judged, CHECK, staged, {}, running=running_here)
        assert (refused.value.reason.fact, refused.value.reason.next_steps) == wanted


def release_source(commit: str) -> str:
    return f"overnightworks-quality @ git+https://github.com/overnightworks/quality@{commit}"


@pytest.mark.parametrize(
    ("reexecution", "flags", "expected"),
    [
        pytest.param(
            ReleaseAtPin(Release("v0.10.1"), pinned_commit()), ("--offline", "--quiet"),
            ["uv", "tool", "run", "--offline", "--quiet", "--from", release_source(pinned_commit())], id="a release, offline",
        ),
        pytest.param(
            ReleaseAtPin(Release("v0.10.1"), pinned_commit()), ("--quiet",),
            ["uv", "tool", "run", "--quiet", "--from", release_source(pinned_commit())], id="a release, online",
        ),
        pytest.param(
            ThisCheckout(Path("/work/quality")), ("--offline", "--quiet"),
            ["uv", "run", "--offline", "--quiet", "--locked", "--project", "/work/quality"], id="quality's checkout",
        ),
    ],
)
def test_a_reexecution_names_the_commit_through_uv(reexecution, flags, expected):
    command = pin.preparation_command(reexecution, *flags)
    assert (command[: len(expected)], command[len(expected) : len(expected) + 3]) == (expected, ["python", "-I", "-c"])


def test_a_guard_quality_never_sets_is_refused(tmp_path):
    running = vcs("0.10.0", pinned_commit())(None)
    with pytest.raises(CannotJudge) as refused:
        pin.judging_release(tmp_path, CHECK, False, {pin.RUN_AT_PIN_VARIABLE: "yes"}, running=running)
    assert (refused.value.reason.fact, refused.value.reason.next_steps) == (
        f"{pin.RUN_AT_PIN_VARIABLE} holds yes, which quality never sets", (Instruction(f"unset {pin.RUN_AT_PIN_VARIABLE}, then run {CHECK} again"),),
    )


def child_refusal(version: str, at: str) -> tuple[str, tuple]:
    fact = f"the gate pins 5e5e5e5 as v0.10.1; uv prepared quality {version} at {at}"
    return fact, (Command("uv cache clean overnightworks-quality"), Instruction(f"then run {CHECK} again"))


@pytest.mark.integration
@pytest.mark.parametrize("fetch", list(pin.Fetch))
def test_the_child_judges_at_the_pins_commit_and_release(build_repo, fetch):
    """PIN-1, PIN-5, PIN-6: a run that is the pinned commit judges at its release, and its row says `fetched now · `
    where the run fetched it."""
    judged = build_repo(gate_call(pinned_commit(), "v0.10.1"))
    running = vcs("0.10.1", pinned_commit())(None)
    fetched = "fetched now · " if fetch is pin.Fetch.FETCHED else ""
    judging = pin.judging_release(judged, CHECK, False, {pin.RUN_AT_PIN_VARIABLE: fetch.value}, running=running)
    assert judging == JudgingRelease(Release("v0.10.1"), f"{fetched}pinned in .github/workflows/ci.yml")


@pytest.mark.integration
@pytest.mark.parametrize("fetch", list(pin.Fetch))
@pytest.mark.parametrize(
    ("running", "expected"),
    [
        pytest.param(vcs("0.10.1", other_commit()), child_refusal("0.10.1", "4d4d4d4"), id="another commit"),
        pytest.param(
            lambda scene: RunningQuality("0.10.1", Path(pin.__file__).parent, None), child_refusal("0.10.1", "an unknown commit"), id="no recorded commit",
        ),
        pytest.param(
            vcs("0.10.2", pinned_commit()),
            (
                "the gate pins 5e5e5e5 as v0.10.1, but that commit is quality 0.10.2",
                (Instruction("make the pin's comment name its commit's own tag, as docs/gate.md shows"),),
            ),
            id="the pin's commit at another release",
        ),
    ],
)
def test_the_child_never_reexecutes(build_repo, fetch, running, expected):
    judged = build_repo(gate_call(pinned_commit(), "v0.10.1"))
    guard = {pin.RUN_AT_PIN_VARIABLE: fetch.value}
    running_quality = running(None)
    with pytest.raises(CannotJudge) as refused:
        pin.judging_release(judged, CHECK, False, guard, running=running_quality)
    assert (refused.value.reason.fact, refused.value.reason.next_steps) == expected


def scripts_with_quality(tmp_path: Path, mode: int = 0o755) -> Path:
    scripts = tmp_path / "bin"
    scripts.mkdir()
    (scripts / "quality").write_text("#!/bin/sh\n")
    (scripts / "quality").chmod(mode)
    return scripts


@pytest.mark.parametrize(
    ("answer", "valid"),
    [
        pytest.param(lambda tmp_path: f"{scripts_with_quality(tmp_path)}\n", True, id="a scripts directory"),
        pytest.param(lambda tmp_path: f"{scripts_with_quality(tmp_path)}\n{tmp_path}\n", False, id="two lines"),
        pytest.param(lambda tmp_path: "bin\n", False, id="a relative path"),
        pytest.param(lambda tmp_path: f"{tmp_path}\n", False, id="no quality in it"),
        pytest.param(lambda tmp_path: f"{scripts_with_quality(tmp_path, 0o644)}\n", False, id="a quality that is not executable"),
    ],
)
def test_the_prepared_environment_s_answer_is_checked(tmp_path, answer, valid):
    prepared = pin.prepared_quality(answer(tmp_path))
    assert prepared == (tmp_path / "bin" / "quality" if valid else None)


def cannot_start(cause: BaseException) -> process.ProcessError:
    error = process.ProcessError(f"cannot run uv: {cause}")
    error.__cause__ = cause
    return error


def uv_command() -> str:
    return f"uv tool run --from '{release_source(pinned_commit())}' quality --version"


def prepare_steps() -> tuple:
    return (Command(uv_command()), Instruction(f"{CHECK} again"))


AT_PIN = ReleaseAtPin(Release("v0.10.1"), pinned_commit())


@pytest.mark.parametrize(
    ("failed", "reexecution", "expected"),
    [
        pytest.param(
            pin.Failed(pin.Failure.UNREACHABLE), AT_PIN,
            ("cannot fetch quality v0.10.1, which the gate pins · github.com did not answer", (Instruction(f"{CHECK}, once online"),)),
            id="the tag query did not answer",
        ),
        pytest.param(
            pin.Failed(pin.Failure.TAG_ELSEWHERE, other_commit()), AT_PIN,
            (
                "the gate pins 5e5e5e5 as v0.10.1, but quality's v0.10.1 is 4d4d4d4",
                (Instruction("make the pin's comment name its commit's own tag, as docs/gate.md shows"),),
            ),
            id="the tag names another commit",
        ),
        pytest.param(
            pin.Failed(pin.Failure.TAG_ELSEWHERE), AT_PIN,
            (
                "the gate pins 5e5e5e5 as v0.10.1, but quality has no release v0.10.1",
                (Instruction("make the pin's comment name its commit's own tag, as docs/gate.md shows"),),
            ),
            id="no such tag",
        ),
        pytest.param(
            pin.Failed.of(cannot_start(FileNotFoundError(2, "No such file or directory"))), AT_PIN,
            ("cannot fetch quality v0.10.1, which the gate pins · uv is not installed", (Instruction(f"install uv, then run {CHECK} again"),)),
            id="uv missing",
        ),
        pytest.param(
            pin.Failed.of(cannot_start(PermissionError(13, "Permission denied"))), AT_PIN,
            ("cannot fetch quality v0.10.1, which the gate pins · cannot run uv: [Errno 13] Permission denied", (Command(uv_command()),)),
            id="uv cannot start",
        ),
        pytest.param(
            pin.Failed(pin.Failure.UV_EXITED, "1"), AT_PIN, ("cannot prepare quality v0.10.1, which the gate pins · uv exited 1", prepare_steps()),
            id="uv exited non-zero",
        ),
        pytest.param(
            pin.Failed.of(process.ProcessError("uv did not finish")), AT_PIN,
            (f"cannot prepare quality v0.10.1, which the gate pins · uv did not finish within {pin.PREPARE_TIMEOUT_SECONDS} seconds", prepare_steps()),
            id="uv did not finish",
        ),
        pytest.param(
            pin.Failed(pin.Failure.NO_EXECUTABLE), AT_PIN,
            ("cannot prepare quality v0.10.1, which the gate pins · uv's environment holds no quality executable", prepare_steps()),
            id="no quality executable",
        ),
        pytest.param(
            pin.Failed(pin.Failure.CANNOT_START, "No such file or directory"), AT_PIN,
            ("cannot start quality v0.10.1 · No such file or directory", (Instruction(f"{CHECK} again"),)), id="the exec failed",
        ),
        pytest.param(
            pin.Failed(pin.Failure.UV_EXITED, "2"), ThisCheckout(Path("/work/quality")),
            ("cannot prepare this checkout · uv exited 2", (Command(f"uv run {CHECK}"),)), id="the checkout",
        ),
    ],
)
def test_a_failed_preparation_is_its_pair(failed, reexecution, expected):
    refusal = pin.preparation_refusal(failed, reexecution, CHECK)
    assert (refusal.reason.fact, refusal.reason.next_steps) == expected


@pytest.mark.parametrize(
    ("environment", "expected"),
    [
        pytest.param({"XDG_CACHE_HOME": "/cache", "HOME": "/home/me"}, Path("/cache/overnightworks-quality/releases"), id="an absolute XDG_CACHE_HOME"),
        pytest.param({"XDG_CACHE_HOME": "cache", "HOME": "/home/me"}, Path("/home/me/.cache/overnightworks-quality/releases"), id="a relative one"),
        pytest.param({"HOME": "/home/me"}, Path("/home/me/.cache/overnightworks-quality/releases"), id="HOME alone"),
        pytest.param({}, None, id="neither"),
    ],
)
def test_the_record_directory_follows_the_cache_variables(environment, expected):
    assert pin.release_records(environment) == expected


def test_a_record_names_one_commit_and_one_release(tmp_path):
    records = tmp_path / "records"
    pin.keep_record(records, pinned_commit(), "v0.10.1")
    locked = tmp_path / "locked"
    locked.mkdir(mode=0o500)
    pin.keep_record(locked / "records", pinned_commit(), "v0.10.1")
    assert (
        pin.is_recorded(records, pinned_commit(), "v0.10.1"),
        pin.is_recorded(records, pinned_commit(), "v0.10.0"),
        pin.is_recorded(None, pinned_commit(), "v0.10.1"),
        pin.is_recorded(locked / "records", pinned_commit(), "v0.10.1"),
    ) == (True, False, False, False)
