import datetime
import os
import stat
from pathlib import Path

import pytest

from quality import advisories, deadline, process
from tests.support.advisories import OSV, advisory, reporting, tracked_lock_repo, uv_lock, zipped
from tests.support.check import is_clean
from tests.support.door import check_door
from tests.support.scanners import OUTPUTS, fake_scanner

NPM_LOCK = "pnpm-lock.yaml"
NEW_ADVISORY = advisory("GHSA-dddd-eeee-ffff")
MISSING = object()


@pytest.fixture
def copy_of(cache_home):
    """Where the copy of an ecosystem's archive lives in this test's user cache directory."""
    cache = process.user_cache_directory({"XDG_CACHE_HOME": str(cache_home)})
    return lambda ecosystem="PyPI": advisories.cached_archive(cache, ecosystem)


@pytest.fixture
def run(build_repo, run_gate):
    """Runs the gate over a repository with a uv.lock; returns (exit code, output)."""
    repo = tracked_lock_repo(build_repo)
    return lambda: run_gate(repo)


@pytest.fixture
def scanned(served_scanners, tmp_path):
    """The archive osv-scanner finds in its database directory at each run, as its bytes; `before_reading` is shell
    that runs first."""
    seen = tmp_path / "scanned.zip"

    def watch(before_reading: str = "") -> Path:
        script = (
            'for argument; do case "$argument" in --local-db-path=*) database="${argument#--local-db-path=}";; esac; done\n'
            f'{before_reading}\ncp "$database/osv-scalibr/PyPI/all.zip" {seen}\n'
            + reporting(OUTPUTS / "osv-scanner-clean.json", 0)
        )
        fake_scanner(served_scanners, OSV, script)
        return seen

    return watch


def statuses(published_databases) -> list[int]:
    return [asked.status for asked in published_databases.asked]


def published_bytes(database_root: Path) -> bytes:
    return (database_root / "PyPI" / "all.zip").read_bytes()


def test_an_unchanged_archive_is_not_downloaded_again(run, published_databases, copy_of, database_root):
    """ADVISORY-44: the server confirms the copy: the second run sends its hash, receives no body and judges as the first."""
    first, second = run(), run()
    assert (first[0], second[0], is_clean(second[1])) == (0, 0, True)
    assert statuses(published_databases) == [200, 304]
    assert copy_of().read_bytes() == published_bytes(database_root)
    assert [stat.S_IMODE(level.stat().st_mode) for level in (copy_of().parent.parent, copy_of().parent)] == [0o700, 0o700]


def test_a_changed_archive_is_downloaded_and_replaces_the_copy(run, published_databases, publish_database, copy_of, database_root):
    """ADVISORY-44: the server states another hash: the archive comes in full, the copy becomes the new bytes and the next run reuses it."""
    run()
    publish_database([NEW_ADVISORY])
    run()
    assert copy_of().read_bytes() == published_bytes(database_root)
    run()
    assert statuses(published_databases) == [200, 200, 304]


@pytest.mark.parametrize("damage", [lambda genuine: genuine[:-30], lambda genuine: genuine + b"x"], ids=["truncated", "altered"])
@pytest.mark.parametrize(
    ("server_says", "last_request_is_plain"), [("its hash differs", False), ("unchanged to any request", True)]
)
def test_a_copy_that_is_not_the_servers_archive_is_never_used(
    run, scanned, published_databases, copy_of, database_root, damage, server_says, last_request_is_plain
):
    """ADVISORY-44, ADVISORY-45: a truncated or altered copy is replaced by the genuine archive, which is what is scanned, whether the server
    answers with the archive at once or claims `unchanged` with the genuine ETag (then by one plain request)."""
    seen = scanned()
    run()
    genuine = published_bytes(database_root)
    copy_of().write_bytes(damage(genuine))
    if server_says == "unchanged to any request":
        published_databases.not_modified = lambda sent, current: sent is not None
    code, _ = run()
    assert (code, seen.read_bytes(), copy_of().read_bytes()) == (0, genuine, genuine)
    assert (published_databases.asked[-1].if_none_match is None) == last_request_is_plain


def test_a_second_ecosystem_is_judged_on_its_own_hash(build_repo, run_gate, publish_database, published_databases):
    """One archive changed and one not: the changed one is downloaded and the other reused."""
    repo = build_repo({"uv.lock": uv_lock(), NPM_LOCK: "lockfileVersion: '9.0'\n"})
    publish_database(ecosystem="npm")
    run_gate(repo)
    publish_database([NEW_ADVISORY], ecosystem="npm")
    published_databases.asked.clear()
    code, _ = run_gate(repo)
    assert code == 0
    assert {(asked.path, asked.status) for asked in published_databases.asked} == {("/PyPI/all.zip", 304), ("/npm/all.zip", 200)}


@pytest.mark.parametrize("etag", [MISSING, 'W/"abc"', '"not-hex"', f'"{"0" * 32}"'], ids=["none", "weak", "not hex", "another body's hash"])
def test_a_download_whose_hash_cannot_be_compared_is_exit_2_and_keeps_nothing(run, published_databases, copy_of, etag):
    """ADVISORY-45: once a copy exists, a body whose ETag states no md5 of its own bytes cannot be compared."""
    run()
    genuine = copy_of().read_bytes()
    published_databases.etag = lambda body: None if etag is MISSING else etag
    published_databases.not_modified = lambda sent, current: False
    code, output = run()
    assert (code, "PyPI advisory database" in output, copy_of().read_bytes()) == (2, True, genuine)


def test_a_body_that_is_not_its_etags_md5_is_exit_2_and_kept_nowhere(run, published_databases, copy_of):
    """ADVISORY-45: corruption in transit: nothing is scanned and no copy is written."""
    published_databases.etag = lambda body: f'"{"0" * 32}"'
    code, output = run()
    assert (code, "PyPI advisory database" in output, copy_of().exists()) == (2, True, False)


@pytest.mark.parametrize("etag", [None, 'W/"abc"'], ids=["missing", "weak"])
def test_a_download_with_no_md5_etag_and_no_copy_is_judged_and_kept_nowhere(
    run, scanned, published_databases, copy_of, database_root, etag
):
    """ADVISORY-45: with no copy, a body without an md5 ETag is scanned and kept nowhere."""
    published_databases.etag = lambda body: etag
    seen = scanned()
    code, _ = run()
    assert (code, seen.read_bytes(), copy_of().exists()) == (0, published_bytes(database_root), False)


@pytest.mark.parametrize("etag", [None, 'W/"abc"'], ids=["without an ETag", "with a weak ETag"])
def test_a_not_modified_without_an_md5_etag_is_exit_2(run, published_databases, copy_of, etag):
    """ADVISORY-45: the server confirms nothing the copy could be compared with."""
    run()
    genuine = copy_of().read_bytes()
    published_databases.etag = lambda body: etag
    published_databases.not_modified = lambda sent, current: True
    code, output = run()
    assert (code, "PyPI advisory database" in output, copy_of().read_bytes()) == (2, True, genuine)


def test_a_not_modified_answering_a_request_with_no_hash_is_exit_2_and_keeps_nothing(run, published_databases, copy_of):
    """ADVISORY-45: a 304 to a request that sent no copy confirms nothing."""
    published_databases.not_modified = lambda sent, current: True
    code, output = run()
    assert (code, "PyPI advisory database" in output, copy_of().exists()) == (2, True, False)


def test_a_server_that_does_not_answer_is_exit_2_even_with_a_valid_copy(run, monkeypatch, copy_of):
    """A copy is never used unverified."""
    run()
    genuine = copy_of().read_bytes()
    monkeypatch.setattr(advisories, "DATABASE_ROOT", "file:///no/such/root")
    code, output = run()
    assert (code, "PyPI advisory database" in output, copy_of().read_bytes()) == (2, True, genuine)


def test_a_cache_directory_that_cannot_be_written_is_a_run_without_a_copy(run, published_databases, cache_home):
    """ADVISORY-46: a cache directory that cannot be created keeps no copy and changes no verdict."""
    blocker = cache_home / "overnightworks-quality"
    blocker.write_text("not a directory")
    results = [run(), run()]
    assert ([code for code, _ in results], statuses(published_databases), blocker.read_text()) == (
        [0, 0], [200, 200], "not a directory")
    assert all(is_clean(output) for _, output in results)


def test_a_copy_that_cannot_be_read_counts_as_none_and_is_replaced(run, published_databases, copy_of, database_root):
    """ADVISORY-43, ADVISORY-46: a copy that cannot be opened is no copy."""
    copy_of().parent.mkdir(parents=True)
    copy_of().symlink_to(copy_of())  # a link to itself: opening it fails
    code, _ = run()
    assert (code, statuses(published_databases), copy_of().read_bytes()) == (0, [200], published_bytes(database_root))


@pytest.mark.parametrize("kind", ["directory", "fifo"])
def test_a_cache_file_that_is_not_a_regular_file_is_no_copy(run, published_databases, copy_of, kind):
    """ADVISORY-43: a directory or a FIFO in place of the copy is no copy, and the run neither hangs nor fails."""
    copy_of().parent.mkdir(parents=True)
    if kind == "directory":
        copy_of().mkdir()
    else:
        os.mkfifo(copy_of())
    code, output = run()
    assert (code, is_clean(output), statuses(published_databases)) == (0, True, [200])
    assert published_databases.asked[-1].if_none_match is None


def test_without_a_usable_cache_base_every_run_downloads(build_repo, published_databases):
    """ADVISORY-43: with no absolute `XDG_CACHE_HOME` or `HOME`, no copy is kept and every run downloads."""
    repo = tracked_lock_repo(build_repo)
    codes = [check_door(repo, environment=os.environ | {"XDG_CACHE_HOME": "relative", "HOME": "also/relative"}) for _ in range(2)]
    assert (codes, statuses(published_databases)) == ([0, 0], [200, 200])
    assert not (repo / "relative").exists()
    assert not (repo / "also").exists()


@pytest.mark.parametrize(
    "archive",
    [b"not a zip", zipped({"readme.txt": "x"}), zipped({"a.json": "{not json"})],
    ids=["not a zip", "no advisory", "not json"],
)
def test_an_archive_that_cannot_be_used_is_never_kept(run, publish_database, copy_of, archive):
    """ADVISORY-44: an archive that is not a valid zip with an advisory is exit 2 and kept nowhere."""
    publish_database(archive=archive)
    code, _ = run()
    assert (code, copy_of().exists()) == (2, False)


def test_the_scanner_reads_the_verified_bytes_although_the_copy_changes_after_the_check(run, scanned, copy_of, database_root):
    """ADVISORY-44: osv-scanner reads a private copy of the checked bytes, so a change of the cache file during the scan changes nothing."""
    run()
    seen = scanned(before_reading=f"printf altered > {copy_of()}")
    code, _ = run()
    assert (code, seen.read_bytes(), copy_of().read_bytes()) == (0, published_bytes(database_root), b"altered")


def test_no_run_leaves_a_temporary_file_and_a_failed_replacement_keeps_no_copy(run, copy_of, monkeypatch):
    """ADVISORY-44, ADVISORY-46: the temporary file has its fixed prefix and goes on every exit path."""
    names = []

    def refused(source, destination):
        names.extend(path.name for path in Path(source).parent.iterdir())
        raise OSError("refused")

    monkeypatch.setattr(os, "replace", refused)
    code, _ = run()
    assert code == 0
    assert (len(names) == 1 and names[0].startswith(advisories.CACHE_TEMPORARY_PREFIX), list(copy_of().parent.iterdir())) == (True, [])


def test_the_hash_of_an_archive_is_its_md5():
    """ADVISORY-44: the digest is the md5 the server states, whatever the fake server computes with it."""
    assert advisories.md5_digest(b"abc") == "900150983cd24fb0d6963f7d28e17f72"


def test_reading_the_copy_does_not_spend_the_time_of_the_requests(
    build_repo, served_scanners, published_databases, database_root, tmp_path, monkeypatch
):
    """ADVISORY-9: a copy that takes the whole budget to read and hash still gets its conditional request, because
    that time belongs to the run alone."""
    cache = tmp_path / "cache"
    advisories.cached_archive(cache, "PyPI").parent.mkdir(parents=True)
    advisories.cached_archive(cache, "PyPI").write_bytes(published_bytes(database_root))
    now = 0.0
    digest = advisories.md5_digest
    hashed = []

    def slow_digest(data: bytes) -> str:
        nonlocal now
        if not hashed:
            now = advisories.DATABASE_DOWNLOAD_DEADLINE_SECONDS
        hashed.append(data)
        return digest(data)

    monkeypatch.setattr(advisories, "md5_digest", slow_digest)
    repo = tracked_lock_repo(build_repo)
    advisories.scan(
        {"uv.lock": repo / "uv.lock"}, repo, [], datetime.date(2026, 10, 21), deadline.RunDeadline.starting(lambda: now),
        cache=cache,
    )
    assert statuses(published_databases) == [304]
