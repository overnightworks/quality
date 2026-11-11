"""The SonarCloud job's install step, `quality sonar scanner`: the pinned scanner CLI is downloaded, its signature is
checked against the key file quality ships, and it is installed where the scan action finds it, so the action never
fetches a key. It prints one row and one closing line, or the pair."""

import posixpath
import re
import shutil
import stat
import tempfile
import time
import zipfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from subprocess import CompletedProcess

from quality import output, process, web
from quality.report import PUSH_THE_FIX, RERUN, CannotJudge, Instruction, Reason
from quality.sonar import EXIT_CLEAN, SonarEvent, refused

# One deadline for the whole command; the install step's limit in gate.yml covers it and the start-up of uv and Python.
COMMAND_DEADLINE_SECONDS = 100
OPERATION_TIMEOUT_SECONDS = 15
GPG_TIMEOUT_SECONDS = 10
UNZIP_TIMEOUT_SECONDS = 15
MAXIMUM_LINK_FOLLOWS = 40  # the Linux kernel stops at 40 links in one path
RETRY_PAUSE_SECONDS = 3
BINARIES_BASE_URL = "https://binaries.sonarsource.com/Distribution/sonar-scanner-cli"
INSTALL_STEP = "Install the pinned scanner"
VERSION_VARIABLE = "SCANNER_VERSION"
FINGERPRINT_VARIABLE = "SCANNER_SIGNER_FINGERPRINT"
TOOL_CACHE_VARIABLE = "RUNNER_TOOL_CACHE"
KEY_FILE_NAME = "sonarsource-scanner-key.asc"
KEY_FILE = Path(__file__).with_name(KEY_FILE_NAME)
VERSION_PATTERN = re.compile(r"(\d+)\.(\d+)\.(\d+)\.(\d+)")
FINGERPRINT_PATTERN = re.compile(r"[0-9A-Fa-f]{40}")
# The directory layout of SonarSource/sonarqube-scan-action's tool cache lookup for the Linux runner.
CACHE_DIRECTORY = "sonar-scanner-cli"
PLATFORM = "linux-x64"
COMPLETE_SUFFIX = ".complete"
STATUS_PREFIX = "[GNUPG:] "
ANOTHER_RELEASE = Instruction(f"set {VERSION_VARIABLE} in gate.yml to another release of the scanner")
RERUN_THEN_ANOTHER_RELEASE = Instruction(f"{RERUN}; when the same line comes again, {ANOTHER_RELEASE}")
RELEASE_OF_A_VALID_SUBKEY = Instruction(
    f"set {VERSION_VARIABLE} in gate.yml to a release signed by a subkey that has neither expired nor been revoked, "
    f"then re-export src/quality/{KEY_FILE_NAME}"
)
REPLACE_THE_KEY = Instruction(f"replace src/quality/{KEY_FILE_NAME} with SonarSource's current public key")
# gpg prints VALIDSIG and exits 0 for a signature by an expired key too, so the exit status never decides. It prints
# KEYEXPIRED for any expired subkey in the file, also one that did not sign, so only the signature's own status counts.
# Replacing the key file cannot mend a signature a revoked subkey made or one the zip's bytes broke, so those two name the
# step that can.
REFUSED_STATUSES = {
    "REVKEYSIG": ("the signing key has been revoked", RELEASE_OF_A_VALID_SUBKEY),
    "BADSIG": ("the signature does not match the zip", RERUN_THEN_ANOTHER_RELEASE),
    "ERRSIG": ("the signature is by a key the file does not hold", REPLACE_THE_KEY),
}
EXPIRED_SIGNING_KEY = "EXPKEYSIG"
Fetch = Callable[[str, Callable[[Exception], CannotJudge], float, Callable[[], float], float], bytes]


@dataclass(frozen=True)
class Budget:
    """What is left of the command's one deadline, on the injected clock."""

    expires_at: float
    clock: Callable[[], float]
    seconds: float

    def left(self) -> float:
        return self.expires_at - self.clock()

    def allow(self, own_limit: float, program: str) -> float:
        """The smaller of `own_limit` and the time left; a spent deadline names `program`, which then does not start."""
        left = self.left()
        if left <= 0:
            raise CannotJudge(f"the deadline of {self.seconds:g}s is spent before {program} could run", (RERUN,))
        return min(own_limit, left)


def _input(environment: Mapping[str, str], variable: str, pattern: re.Pattern[str], expected: str) -> re.Match[str]:
    value = environment.get(variable)
    sets_it = f'the step "{INSTALL_STEP}" of gate.yml sets it'
    if value is None:
        raise CannotJudge(f"{variable} is not set; {sets_it}", (PUSH_THE_FIX,))
    match = pattern.fullmatch(value)
    if match is None:
        raise CannotJudge(f"{variable} is {value!r}, not {expected}; {sets_it}", (PUSH_THE_FIX,))
    return match


def _tool_cache(environment: Mapping[str, str]) -> Path:
    value = environment.get(TOOL_CACHE_VARIABLE)
    if not value:
        raise CannotJudge(f'{TOOL_CACHE_VARIABLE} is not set; the step "{INSTALL_STEP}" runs on a GitHub runner', (PUSH_THE_FIX,))
    return Path(value)


def _program(run: process.Run, budget: Budget, command: list[str], own_limit: float) -> CompletedProcess:
    timeout = budget.allow(own_limit, command[0])
    try:
        return run(command, timeout=timeout)
    except process.ProcessError as error:
        raise CannotJudge(str(error), (RERUN,)) from error


def _key_file_fault(listing: str, fingerprint: str) -> str | None:
    """Why the key file is not exactly the pinned key, from `gpg --with-colons --show-keys`; None when it is."""
    records = [line.split(":") for line in listing.splitlines()]
    primaries = [
        (record[1], following[9].upper())
        for record, following in pairwise(records)
        if record[0] == "pub" and following[0] == "fpr" and len(following) > 9
    ]
    if len(primaries) != 1:
        return f"{KEY_FILE_NAME} holds {len(primaries)} primary keys, not one"
    validity, actual = primaries[0]
    if actual != fingerprint:
        return f"{KEY_FILE_NAME} holds the primary key {actual}, not the {fingerprint} that {FINGERPRINT_VARIABLE} names"
    if validity in ("e", "r"):
        return f"the primary key in {KEY_FILE_NAME} has {'expired' if validity == 'e' else 'been revoked'}"
    return None


def _signature_fault(status: str, fingerprint: str) -> tuple[str, Instruction] | None:
    """Why the zip's signature does not verify and what to do about it, from gpg's status lines alone; None when it does."""
    words = [line.removeprefix(STATUS_PREFIX).split() for line in status.splitlines() if line.startswith(STATUS_PREFIX)]
    names = {word[0] for word in words if word}
    if EXPIRED_SIGNING_KEY in names:
        return f"gpg reports {EXPIRED_SIGNING_KEY}: the subkey that signed it has expired", RELEASE_OF_A_VALID_SUBKEY
    for refused_status, (reason, remedy) in REFUSED_STATUSES.items():
        if refused_status in names:
            return f"gpg reports {refused_status}: {reason}", remedy
    if "GOODSIG" not in names:
        return "gpg does not report a good signature", REPLACE_THE_KEY
    # The first field of VALIDSIG is the signing subkey; the last is the primary key, which is the one pinned.
    if not any(word[0] == "VALIDSIG" and word[-1].upper() == fingerprint for word in words):
        return f"the signature is not by the primary key {fingerprint}", REPLACE_THE_KEY
    return None


def _download(url: str, fetch: Fetch, budget: Budget, sleep: web.Sleep) -> bytes:
    def unreadable(error: Exception) -> CannotJudge:
        timed_out = isinstance(error, OSError) and web.timed_out(error)
        return CannotJudge(f"could not download {url}: {'no answer in time' if timed_out else error}", (RERUN,))

    while True:
        try:
            return fetch(url, unreadable, budget.expires_at, budget.clock, OPERATION_TIMEOUT_SECONDS)
        except CannotJudge:
            if budget.left() <= 0:
                raise
            sleep(min(RETRY_PAUSE_SECONDS, budget.left()))


def _stays_inside(name: str, folder: str) -> bool:
    return "\\" not in name and f"{posixpath.normpath(name)}/".startswith(f"{folder}/")


def _stepped(resolved: tuple[str, ...], part: str, folder: str) -> tuple[str, ...] | None:
    """`resolved` after one more path component, None when that leaves `folder`."""
    if part == "..":
        return resolved[:-1] if len(resolved) > 1 else None
    stepped = (*resolved, part)
    return stepped if stepped[0] == folder else None


def _leaves_through_links(path: str, links: Mapping[str, str], folder: str) -> bool:
    """Whether following `path` (from the zip's root) through the archive's own symbolic links `links` (name to target)
    ends outside `folder`, or never ends because the links loop; this is how the filesystem resolves it once unpacked.
    A link with an absolute target is rooted outside the unpacked directory, whatever its first component."""
    resolved: tuple[str, ...] = ()
    pending = path.split("/")
    follows = 0
    while pending:
        part = pending.pop(0)
        if part in ("", "."):
            continue
        if (stepped := _stepped(resolved, part, folder)) is None:
            return True
        resolved = stepped
        if (target := links.get("/".join(resolved))) is None:
            continue
        follows += 1
        if target.startswith("/") or follows > MAXIMUM_LINK_FOLLOWS:
            return True
        resolved = resolved[:-1]
        pending = [*target.split("/"), *pending]
    return False


def _escaping_entry(archive: Path, folder: str) -> str | None:
    """The first entry of the zip that would land outside `folder`, a symbolic link whose target does included; None when
    every entry stays inside."""
    try:
        with zipfile.ZipFile(archive) as opened:
            entries = opened.infolist()
            links = {
                posixpath.normpath(entry.filename): opened.read(entry).decode("utf-8", "replace")
                for entry in entries
                if stat.S_ISLNK(entry.external_attr >> 16)
            }
    except zipfile.BadZipFile as error:
        raise CannotJudge(f"the scanner zip is not a readable zip: {error}", (ANOTHER_RELEASE,)) from error
    for entry in entries:
        if not _stays_inside(entry.filename, folder):
            return entry.filename
        name = posixpath.normpath(entry.filename)
        if (target := links.get(name)) is not None and (
            target.startswith("/") or _leaves_through_links(posixpath.join(posixpath.dirname(name), target), links, folder)
        ):
            return f"{entry.filename} -> {target}"
    return None


def _unpacked_into_place(run: process.Run, budget: Budget, archive: Path, folder: str, target: Path) -> None:
    """The scanner appears at `target` whole: an archive whose entries stay in `folder` is unpacked into a directory beside
    it, then moved."""
    if escaping := _escaping_entry(archive, folder):
        raise CannotJudge(f"the scanner zip holds {escaping}, which leaves its folder {folder}", (ANOTHER_RELEASE,))
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent) as unpacked:
        unzipped = _program(run, budget, ["unzip", "-q", str(archive), "-d", unpacked], UNZIP_TIMEOUT_SECONDS)
        if unzipped.returncode != 0:
            raise CannotJudge(f"unzip stopped with exit {unzipped.returncode} before it unpacked the scanner whole", (RERUN,))
        if target.exists():  # no marker: an earlier run stopped partway
            shutil.rmtree(target)
        (Path(unpacked) / folder).replace(target)


def _install(
    environment: Mapping[str, str], budget: Budget, fetch: Fetch, run: process.Run, sleep: web.Sleep, base_url: str, key_file: Path
) -> output.Row:
    major, minor, patch, build = _input(environment, VERSION_VARIABLE, VERSION_PATTERN, "four dot-separated numbers").groups()
    version = f"{major}.{minor}.{patch}.{build}"
    fingerprint = _input(environment, FINGERPRINT_VARIABLE, FINGERPRINT_PATTERN, "40 hexadecimal characters").group().upper()
    target = _tool_cache(environment) / CACHE_DIRECTORY / f"{major}.{minor}.{patch}-build.{build}" / PLATFORM
    marker = target.with_name(target.name + COMPLETE_SUFFIX)
    if marker.exists():
        return output.Row(output.Glyph.NOTE, "scanner", SonarEvent.CACHED, f"{version} is in the tool cache")
    with process.scratch_directory() as scratch:
        home = scratch / "gpg"
        home.mkdir(mode=0o700)
        gpg = ["gpg", "--homedir", str(home), "--batch"]
        listing = _program(run, budget, [*gpg, "--with-colons", "--show-keys", str(key_file)], GPG_TIMEOUT_SECONDS).stdout
        if fault := _key_file_fault(listing, fingerprint):
            raise CannotJudge(fault, (REPLACE_THE_KEY,))
        url = f"{base_url}/sonar-scanner-cli-{version}-{PLATFORM}.zip"
        archive, signature = scratch / "scanner.zip", scratch / "scanner.zip.asc"
        archive.write_bytes(_download(url, fetch, budget, sleep))
        signature.write_bytes(_download(f"{url}.asc", fetch, budget, sleep))
        _program(run, budget, [*gpg, "--import", str(key_file)], GPG_TIMEOUT_SECONDS)
        status = _program(run, budget, [*gpg, "--status-fd", "1", "--verify", str(signature), str(archive)], GPG_TIMEOUT_SECONDS).stdout
        if fault := _signature_fault(status, fingerprint):
            reason, remedy = fault
            raise CannotJudge(f"{KEY_FILE_NAME} does not verify {url}: {reason}", (remedy,))
        _unpacked_into_place(run, budget, archive, f"sonar-scanner-{version}-{PLATFORM}", target)
    marker.touch()
    return output.Row(output.Glyph.CLEAN, "scanner", SonarEvent.INSTALLED, f"{version} · signature verified by {fingerprint}")


def run_scanner(
    environment: Mapping[str, str], clock: Callable[[], float], channels: output.Channels, *,
    fetch: Fetch = web.fetch_within, run: process.Run = process.run, sleep: web.Sleep = time.sleep,
    base_url: str = BINARIES_BASE_URL, deadline_seconds: float = COMMAND_DEADLINE_SECONDS, key_file: Path = KEY_FILE,
) -> int:
    """Runs before the scan: the pinned scanner is verified against the key file and installed into the runner tool
    cache, then one row and the closing line; a refusal is the pair and installs nothing. The keyword arguments are the
    ports and limits a test replaces."""
    started = clock()
    budget = Budget(started + deadline_seconds, clock, deadline_seconds)
    try:
        row = _install(environment, budget, fetch, run, sleep, base_url, key_file)
    except CannotJudge as refusal:
        return refused(refusal.reason, PUSH_THE_FIX, channels)
    except OSError as error:  # a full or unwritable disk while the scanner is written
        return refused(Reason(f"could not write the scanner: {error}", (RERUN,)), PUSH_THE_FIX, channels)
    output.write_rows((row,), channels.out)
    output.write_closing_line(output.Glyph.CLEAN, ("clean", output.format_duration(clock() - started)), channels.out)
    return EXIT_CLEAN
