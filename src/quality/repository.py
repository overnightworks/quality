"""The one reader of the checked-out repository's own files.

A name is repository-relative, as the caller gives it. Every read stays inside the repository, and every error names
the file by that name alone, never a resolved path or the root, so no message prints where the checkout lies.
It uses os.path.realpath, os.lstat and os.stat rather than Path.resolve and Path.exists: those swallow the errors
this reader must tell apart (a path the filesystem refuses, a link to nowhere, a link loop), while the os calls
raise an OSError whose errno decides.
"""

import errno
import os
import stat
from pathlib import Path

ABSENT_ERRNOS = frozenset({errno.ENOENT, errno.ENOTDIR})
GONE_ERRNOS = ABSENT_ERRNOS | {errno.ELOOP}


class RepositoryFileError(Exception):
    """A file of the repository cannot be used; the message names it repository-relative."""


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


def is_on_disk(root: Path, name: str) -> bool:
    """Whether `name` leads to something; a link to nowhere is gone, a path the filesystem refuses is an error."""
    try:
        os.stat(os.path.join(root, name))
    except OSError as error:
        if error.errno in GONE_ERRNOS:
            return False
        raise RepositoryFileError(f"cannot reach {name}: {os_error_reason(error)}") from error
    return True


def write_text(root: Path, name: str, text: str) -> None:
    """Writes the repository's own `name` as UTF-8, never through a symlink, which could redirect it anywhere."""
    repository = os.path.realpath(root)
    path = Path(repository, name)
    try:
        if os.path.realpath(path) != str(path):
            raise RepositoryFileError(f"cannot write {name}: a symlink on its way would redirect the write")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError as error:
        raise RepositoryFileError(f"cannot write {name}: {os_error_reason(error)}") from error
