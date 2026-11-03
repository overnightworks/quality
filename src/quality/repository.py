"""The one reader of the checked-out repository's own files.

A name is repository-relative, as the caller gives it. Every read stays inside the repository, and every error names
the file by that name alone, never a resolved path or the root, so no message prints where the checkout lies.
It uses os.path.realpath, os.lstat and os.stat, not Path.exists or Path.exists(follow_symlinks=False), which turn every
OSError into False: a path the filesystem refuses (EACCES, ENAMETOOLONG) would read as absent. os.lstat tells an absent
entry from a dangling link, and os.stat raises the OSError whose errno decides, so each refusal keeps its own cause.
"""

import errno
import os
import secrets
import stat
import tomllib
from functools import partial
from pathlib import Path

ABSENT_ERRNOS = frozenset({errno.ENOENT, errno.ENOTDIR})
GONE_ERRNOS = ABSENT_ERRNOS | {errno.ELOOP}
PRIVATE_MODE = 0o600
NEW_FILE_MODE = 0o666


class RepositoryFileError(Exception):
    """A file of the repository cannot be used; the message names it repository-relative."""


class TomlError(Exception):
    """Text that tomllib refuses, for its syntax or for a limit of Python itself; the message is tomllib's reason."""


def os_error_reason(error: OSError) -> str:
    """An OSError as `[Errno N] strerror`, without the filename, which may be a resolved path."""
    return f"[Errno {error.errno}] {error.strerror}"


def resolve_file(root: Path, name: str) -> Path:
    """The regular file `name` leads to, which must lie inside the repository."""
    try:
        repository = os.path.realpath(root)
        target = Path(os.path.realpath(os.path.join(repository, name)))
        if not target.is_relative_to(repository):
            raise RepositoryFileError(f"{name} resolves outside the repository")
        mode = os.stat(target).st_mode
    except OSError as error:
        raise RepositoryFileError(f"cannot read {name}: {os_error_reason(error)}") from error
    if not stat.S_ISREG(mode):
        raise RepositoryFileError(f"{name} is not a file")
    return target


def read_bytes(root: Path, name: str) -> bytes | None:
    """The file's bytes; None only when the tree holds nothing at `name`, so a link to nowhere is an error."""
    try:
        os.lstat(os.path.join(root, name))
    except OSError as error:
        if error.errno in ABSENT_ERRNOS:
            return None
        raise RepositoryFileError(f"cannot read {name}: {os_error_reason(error)}") from error
    path = resolve_file(root, name)
    try:
        # resolve_file has just contained the path in the repository. Sonar does not follow that check across the call,
        # and repeating it here would be a second containment decider, so the path-traversal taint is accepted.
        return path.read_bytes()  # NOSONAR(S8707)
    except OSError as error:
        raise RepositoryFileError(f"cannot read {name}: {os_error_reason(error)}") from error


def read_text(root: Path, name: str) -> str | None:
    """The file as UTF-8 text, None when the tree holds nothing at `name`."""
    content = read_bytes(root, name)
    if content is None:
        return None
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise RepositoryFileError(f"cannot read {name}: {error}") from error


def parse_toml(text: str) -> dict:
    """The TOML document in `text`. tomllib also gives up with ValueError on an oversized integer and RecursionError on deep nesting."""
    try:
        return tomllib.loads(text)
    except (ValueError, RecursionError) as error:
        raise TomlError(str(error)) from error


def is_on_disk(root: Path, name: str) -> bool:
    """Whether `name` leads to something; a link to nowhere is gone, a path the filesystem refuses is an error."""
    try:
        os.stat(os.path.join(root, name))
    except OSError as error:
        if error.errno in GONE_ERRNOS:
            return False
        raise RepositoryFileError(f"cannot reach {name}: {os_error_reason(error)}") from error
    return True


def is_symlink(root: Path, name: str) -> bool:
    """Whether `name` is itself a symbolic link, dangling or not; nothing at `name` is not one."""
    try:
        return stat.S_ISLNK(os.lstat(os.path.join(root, name)).st_mode)
    except OSError as error:
        if error.errno in ABSENT_ERRNOS:
            return False
        raise RepositoryFileError(f"cannot reach {name}: {os_error_reason(error)}") from error


def write_text(root: Path, name: str, text: str) -> None:
    """Writes the repository's own `name` as UTF-8, never through a symlink, which could redirect it anywhere. Whatever
    stops the process leaves the complete old file or the complete new one: the successful replacement is the commit
    point, and an interrupt can still arrive after it and before `os.replace` returns, leaving the new file. Nothing
    is promised against a power loss."""
    repository = os.path.realpath(root)
    path = Path(repository, name)
    try:
        if os.path.realpath(path) != str(path):
            raise RepositoryFileError(f"cannot write {name}: a symlink on its way would redirect the write")
        path.parent.mkdir(parents=True, exist_ok=True)
        _replace_whole(path, text.encode("utf-8"))
    except OSError as error:
        raise RepositoryFileError(f"cannot write {name}: {os_error_reason(error)}") from error


def _mode_to_keep(path: Path) -> int | None:
    """The mode of the file about to be replaced, None where there is none; the open keeps a read-only file refused."""
    try:
        with open(path, "r+b") as file:
            return stat.S_IMODE(os.fstat(file.fileno()).st_mode)
    except FileNotFoundError:
        return None


def _replace_whole(path: Path, content: bytes) -> None:
    kept_mode = _mode_to_keep(path)
    scratch = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    # A `partial` of the C `os.open` runs no Python between the creation and the file object's ownership of the
    # descriptor; a lambda would.
    opener = partial(os.open, mode=NEW_FILE_MODE if kept_mode is None else PRIVATE_MODE)
    try:
        with open(scratch, "xb", opener=opener) as file:
            file.write(content)
            file.flush()
            if kept_mode is not None:
                os.fchmod(file.fileno(), kept_mode)
            os.fsync(file.fileno())
        os.replace(scratch, path)
    except FileExistsError:  # `x` met a file this run did not create, so it is not this run's to delete
        raise
    except BaseException:
        scratch.unlink(missing_ok=True)
        raise
