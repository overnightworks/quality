from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from quality import checkout, pin
from quality.installation import DirectoryOrigin, VcsOrigin
from quality.pin import JudgingRelease, ReleaseEvent, RunningQuality
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


def unknown_commit(pinned: Callable[[Scene], str], version: str = "0.10.0") -> Callable[[Scene], tuple[str, tuple]]:
    def refusal(scene: Scene) -> tuple[str, tuple]:
        commit = pinned(scene)
        fact = f"the gate pins {commit[:7]} as v0.10.0; this is quality {version} whose commit is unknown"
        return fact, (Instruction("run quality v0.10.0, the release the gate pins"),)

    return refusal


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
        lambda scene: ("this is quality 0.10.0, not this checkout's code", (Command("uv run quality check"),)), id="own gate, another installation",
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
        lambda scene: ("the gate pins 5e5e5e5 as v0.10.1; this is quality 0.10.0 at 4d4d4d4", (Instruction("run quality v0.10.1, the release the gate pins"),)),
        id="another release",
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
        lambda scene: ("the gate pins 5e5e5e5 as v0.10.0; this is quality 0.10.0 at 4d4d4d4", (Instruction("run quality v0.10.0, the release the gate pins"),)),
        id="installed 0.10.0, the pin 0.10.1's commit commented v0.10.0",
    ),
    pytest.param(
        pinned_at(at_tagged), (), from_tool(), nothing, False, lambda scene: released("v0.10.0"), id="a clean directory installation at the pinned tag",
    ),
    pytest.param(
        pinned_at(at_later), (), from_tool(), check_out_later, False, unknown_commit(at_later),
        id="a directory installation whose tag names another commit",
    ),
    pytest.param(
        pinned_at(at_tagged), (), from_tool(), inside_a_commit_hook, False, lambda scene: released("v0.10.0"),
        id="a directory installation inside a commit hook",
    ),
    pytest.param(
        pinned_at(at_tagged), (), from_tool(dev_version()), tag_the_dev_version, False, unknown_commit(at_tagged, dev_version()),
        id="a directory installation at a dev version",
    ),
    pytest.param(
        pinned_at(at_tagged), (), from_tool(), change_tracked_source, False, unknown_commit(at_tagged),
        id="a directory installation whose tracked source changed after its install",
    ),
    pytest.param(
        pinned_at(at_tagged), (), from_tool(), add_untracked_file, False, unknown_commit(at_tagged), id="a directory installation with an untracked file",
    ),
    pytest.param(
        pinned_at(at_tagged), (), from_tool(), check_out_later, False, unknown_commit(at_tagged),
        id="a directory installation checked out at another commit",
    ),
    pytest.param(
        pinned_at(at_tagged), (), built_elsewhere, nothing, False, unknown_commit(at_tagged), id="a directory installation built into another place",
    ),
    pytest.param(
        pinned_at(lambda scene: pinned_commit()), (), lambda scene: RunningQuality("0.10.0", Path(pin.__file__).parent, None), nothing, False,
        unknown_commit(lambda scene: pinned_commit()), id="no recorded origin",
    ),
    pytest.param(
        pinned_at(lambda scene: pinned_commit()), (), vcs("0.10.0", pinned_commit()[:7]), nothing, False, unknown_commit(lambda scene: pinned_commit()),
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
    directory, tagged, later = tool
    placeholder = Scene(Path(), directory, tagged, later)
    judged = build_repo(files(placeholder))
    scene = Scene(judged, directory, tagged, later)
    for number, url in enumerate(remotes):
        git(judged, "remote", "add", f"remote{number}", url)
    arrange(scene, monkeypatch)
    wanted = expected(scene)
    running_here = running(scene)
    if isinstance(wanted, JudgingRelease):
        assert pin.judging_release(judged, CHECK, staged, running=running_here) == wanted
    else:
        with pytest.raises(CannotJudge) as refused:
            pin.judging_release(judged, CHECK, staged, running=running_here)
        assert (refused.value.reason.fact, refused.value.reason.next_steps) == wanted
