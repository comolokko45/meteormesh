"""MeteorMesh MM-01: prototype fail-closed, directory-fd pinned storage writer.

Not deployed; not a replacement for integrating/auditing all MeteorHub entrypoints.
Production uses KernelEvidenceSource. Test fixtures may inject MockEvidenceSource.
Python >=3.10, Linux only. No auto-mount, no deletion, no overwriting.
"""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Protocol


class StorageDenied(RuntimeError):
    """Storage preconditions are not met; caller must not attempt fallback writes."""


@dataclass(frozen=True)
class StoragePolicy:
    root: str = "/srv/meteor-data"
    uuid: str = "444a8a6d-090a-456c-8a9d-d99665c32574"
    fstype: str = "ext4"
    # Reserve is a *planning* policy; 150GiB required for starting substantial transfers.
    transfer_reserve_bytes: int = 150 * 1024**3


@dataclass(frozen=True)
class Evidence:
    is_exact_mount: bool
    fstype: str
    uuid: str
    dev: int
    read_only: bool
    free_bytes: int


class EvidenceSource(Protocol):
    def read(self, root: str, root_fd: int, expected_uuid: str) -> Evidence: ...


_MNT_ESCAPE = re.compile(r"\\([0-7]{3})")


def _unescape_mount(s: str) -> str:
    return _MNT_ESCAPE.sub(lambda m: chr(int(m.group(1), 8)), s)


class KernelEvidenceSource:
    """Confirm exact mountinfo record and the device behind the expected UUID.

    Reject bind mounts rooted in a subdirectory and UUID aliases to non-block devices.
    Keep root_fd pinned: subsequent dir_fd operations cannot fall back to SSD root
    even when /srv/meteor-data is concurrently unmounted.
    """

    def read(self, root: str, root_fd: int, expected_uuid: str) -> Evidence:
        root_st = os.fstat(root_fd)
        parent_st = os.stat(os.path.dirname(root))
        try:
            lines = Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise StorageDenied("MOUNTINFO_UNAVAILABLE") from exc
        matches = []
        for line in lines:
            lhs, sep, rhs = line.partition(" - ")
            if not sep:
                continue
            cols, post = lhs.split(), rhs.split()
            if len(cols) < 6 or len(post) < 3:
                continue
            if _unescape_mount(cols[4]) == root:
                matches.append((cols, post))
        if not matches:
            raise StorageDenied("MOUNT_NOT_VERIFIED")
        cols, post = matches[-1]
        try:
            major, minor = (int(x) for x in cols[2].split(":"))
            mount_dev = os.makedev(major, minor)
        except (ValueError, TypeError) as exc:
            raise StorageDenied("BAD_MOUNT_METADATA") from exc
        if mount_dev != root_st.st_dev or root_st.st_dev == parent_st.st_dev:
            raise StorageDenied("MOUNT_DEVICE_MISMATCH")
        if _unescape_mount(cols[3]) != "/":
            raise StorageDenied("BIND_SUBPATH_NOT_ALLOWED")
        link = f"/dev/disk/by-uuid/{expected_uuid}"
        try:
            device_st = os.stat(link)
        except OSError as exc:
            raise StorageDenied("UUID_DEVICE_UNAVAILABLE") from exc
        if not stat.S_ISBLK(device_st.st_mode) or device_st.st_rdev != root_st.st_dev:
            raise StorageDenied("UUID_DEVICE_MISMATCH")
        vfs = os.fstatvfs(root_fd)
        return Evidence(
            is_exact_mount=True,
            fstype=post[0],
            uuid=expected_uuid,
            dev=root_st.st_dev,
            read_only=("ro" in cols[5].split(",") or "ro" in post[2].split(",")
                       or bool(vfs.f_flag & getattr(os, "ST_RDONLY", 1))),
            free_bytes=vfs.f_bavail * vfs.f_frsize,
        )


def _parts(relative: str) -> list[str]:
    if not isinstance(relative, str) or not relative or relative.startswith("/") or "\x00" in relative:
        raise StorageDenied("INVALID_RELATIVE_PATH")
    parts = relative.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise StorageDenied("PATH_TRAVERSAL_OR_EMPTY_PART")
    return parts


class GuardedStorage:
    """Scoped writer for *new* files; never deletes/replaces a file.

    A pinned root fd and O_NOFOLLOW dirfd walk prevent SSD fallback and symlink
    escape. Recheck mount evidence before each write. This deliberately does NOT
    expose a free-form filesystem path, nor adapt SQLite/ffmpeg automatically.
    """

    def __init__(self, policy: StoragePolicy = StoragePolicy(), *, source: EvidenceSource | None = None):
        self.policy = policy
        self.source = source if source is not None else KernelEvidenceSource()
        self._fd: int | None = None
        self._initial_dev: int | None = None
        self._retained_parent_fds: list[int] = []
        # SQLite context managers commit but do NOT close connections.
        # Keep connections alive only until the pinned mount directory is released.
        self._sqlite_connections: list[sqlite3.Connection] = []
        # Hold O_NOFOLLOW-opened database files until SQLite connections close.
        # A procfd URI then gives SQLite a pinned inode, not a swappable name.
        self._sqlite_file_fds: list[int] = []

    def __enter__(self) -> "GuardedStorage":
        root = self.policy.root
        if not root.startswith("/") or root == "/" or root.endswith("/") or "/../" in root or "/./" in root:
            raise StorageDenied("INVALID_MOUNT_ROOT")
        # No mkdir here. Never create the absent mount directory on SSD.
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        try:
            self._fd = os.open(root, flags)
            self._initial_dev = os.fstat(self._fd).st_dev
            self.verify()
            return self
        except (OSError, StorageDenied) as exc:
            self.__exit__(None, None, None)
            if isinstance(exc, StorageDenied):
                raise
            raise StorageDenied("STORAGE_ROOT_OPEN_FAILED") from exc

    def __exit__(self, _type, _value, _tb) -> None:
        # SQLite may checkpoint WAL and touch -shm during close(). Keep all
        # pinned parents and the verified root FD open until after DB closes.
        # This also closes connections left open by `with db:` users, since
        # sqlite3.Connection.__exit__ does not actually close the handle.
        failed = None
        for conn in reversed(self._sqlite_connections):
            try:
                conn.close()
            except sqlite3.Error as ex:
                failed = ex
        self._sqlite_connections.clear()
        for db_fd in reversed(self._sqlite_file_fds):
            os.close(db_fd)
        self._sqlite_file_fds.clear()
        for parent_fd in reversed(self._retained_parent_fds):
            os.close(parent_fd)
        self._retained_parent_fds.clear()
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        if failed is not None and _type is None:
            raise StorageDenied("SQLITE_CLOSE_FAILED") from failed

    def verify(self, required_new_bytes: int = 0, *, require_transfer_reserve: bool = False) -> Evidence:
        if self._fd is None:
            raise StorageDenied("GUARD_CLOSED")
        if required_new_bytes < 0:
            raise StorageDenied("NEGATIVE_SIZE")
        try:
            ev = self.source.read(self.policy.root, self._fd, self.policy.uuid)
        except OSError as exc:
            raise StorageDenied("EVIDENCE_READ_FAILED") from exc
        if not ev.is_exact_mount or ev.fstype != self.policy.fstype or ev.uuid != self.policy.uuid:
            raise StorageDenied("MOUNT_NOT_VERIFIED")
        if ev.dev != self._initial_dev:
            raise StorageDenied("MOUNT_CHANGED")
        if ev.read_only:
            raise StorageDenied("MOUNT_READ_ONLY")
        reserve = self.policy.transfer_reserve_bytes if require_transfer_reserve else 0
        if ev.free_bytes < required_new_bytes + reserve:
            raise StorageDenied("INSUFFICIENT_SPACE")
        return ev

    def _open_parent(self, parent_parts: list[str], *, create: bool = False) -> int:
        assert self._fd is not None
        fd = os.dup(self._fd)
        try:
            for segment in parent_parts:
                self.verify()
                if create:
                    try:
                        os.mkdir(segment, mode=0o700, dir_fd=fd)
                    except FileExistsError:
                        pass
                new_fd = os.open(segment, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                                 dir_fd=fd)
                os.close(fd)
                fd = new_fd
                if os.fstat(fd).st_dev != self._initial_dev:
                    raise StorageDenied("NESTED_MOUNT_NOT_ALLOWED")
            return fd
        except (OSError, StorageDenied) as exc:
            os.close(fd)
            if isinstance(exc, StorageDenied):
                raise
            raise StorageDenied("UNSAFE_PARENT_DIRECTORY") from exc

    def write_new(self, relative: str, chunks: Iterable[bytes], *, expected_bytes: int,
                  require_transfer_reserve: bool = False) -> int:
        """Create exclusive file, write bounded payload; preserve partial on errors.

        New files must have unique staging names. This is NOT a safe resume API.
        A caller must not translate failures into a fallback regular open(path).
        """
        parts = _parts(relative)
        self.verify(expected_bytes, require_transfer_reserve=require_transfer_reserve)
        parent = self._open_parent(parts[:-1], create=False)
        filefd = None
        total = 0
        try:
            self.verify(expected_bytes, require_transfer_reserve=require_transfer_reserve)
            try:
                filefd = os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                                 mode=0o600, dir_fd=parent)
            except OSError as exc:
                raise StorageDenied("EXCLUSIVE_CREATE_FAILED") from exc
            for b in chunks:
                if not isinstance(b, bytes):
                    raise StorageDenied("INVALID_CHUNK_TYPE")
                if total + len(b) > expected_bytes:
                    raise StorageDenied("PAYLOAD_TOO_LARGE")
                self.verify(expected_bytes - total, require_transfer_reserve=require_transfer_reserve)
                cursor = 0
                while cursor < len(b):
                    cursor += os.write(filefd, b[cursor:])
                total += len(b)
            if total != expected_bytes:
                raise StorageDenied("PAYLOAD_SIZE_MISMATCH")
            self.verify()
            os.fsync(filefd)
            os.fsync(parent)
            return total
        finally:
            if filefd is not None:
                os.close(filefd)
            os.close(parent)

# --- MM-01 direct development: pinned-path and SQLite adapters (NOT DEPLOYED) ---
import sqlite3
from urllib.parse import quote


class GuardedSQLiteCursor(sqlite3.Cursor):
    def execute(self, sql, parameters=()):
        self.connection._mm01_verify_db_inode()
        return super().execute(sql, parameters)

    def executemany(self, sql, seq_of_parameters):
        self.connection._mm01_verify_db_inode()
        return super().executemany(sql, seq_of_parameters)

    def executescript(self, sql_script):
        self.connection._mm01_verify_db_inode()
        return super().executescript(sql_script)


class GuardedSQLiteConnection(sqlite3.Connection):
    """Check the live mount before direct SQL operations; path is directory-FD pinned.

    This supplements, but cannot replace, source-level review of all SQLite and
    subprocess writes. A live mount loss can make SQLite fail during execution.
    """

    def _mm01_verify_db_inode(self):
        self._mm01_guard.verify()
        try:
            item = os.stat(self._mm01_pinned_path, follow_symlinks=False)
        except OSError as ex:
            raise StorageDenied('DB_PATH_LOST') from ex
        if (not stat.S_ISREG(item.st_mode) or
                (item.st_dev, item.st_ino) != self._mm01_db_inode):
            raise StorageDenied('DB_PATH_CHANGED')

    def cursor(self, factory=GuardedSQLiteCursor):
        return super().cursor(factory=factory)

    def execute(self, sql, parameters=()):
        return self.cursor().execute(sql, parameters)

    def executemany(self, sql, seq_of_parameters):
        return self.cursor().executemany(sql, seq_of_parameters)

    def executescript(self, sql_script):
        self._mm01_verify_db_inode()
        return super().executescript(sql_script)

    def commit(self):
        self._mm01_verify_db_inode()
        return super().commit()

    def rollback(self):
        self._mm01_verify_db_inode()
        return super().rollback()

    def __exit__(self, exc_type, exc_val, exc_tb):
        # sqlite3.Connection.__exit__ can perform rollback on exceptions.
        # Both the COMMIT and ROLLBACK paths need mount/inode verification.
        self._mm01_verify_db_inode()
        return super().__exit__(exc_type, exc_val, exc_tb)


def _relative_path(root: Path, target: Path) -> str:
    """Strict root-relative path conversion, without resolving symlinks."""
    try:
        rel = target.relative_to(root)
    except ValueError as exc:
        raise StorageDenied("TARGET_OUTSIDE_MOUNT") from exc
    rels = rel.as_posix()
    _parts(rels)
    return rels


def _guarded_target(self: GuardedStorage, target: Path | str, *, create_parents: bool = False,
                    may_exist: bool = False) -> str:
    """Return a path pinned to the opened filesystem, not the SSD mount directory.

    Every untrusted parent is verified using O_NOFOLLOW and st_dev. The returned
    path requires the GuardedStorage object to remain open until the operation ends.
    SQLite WAL/SHM paths inherit the same /proc/self/fd/<dirfd> parent.
    """
    if self._fd is None:
        raise StorageDenied("GUARD_CLOSED")
    p = Path(target)
    if p.is_absolute():
        relative = _relative_path(Path(self.policy.root), p)
    else:
        relative = p.as_posix()
    parts = _parts(relative)
    self.verify()
    if len(self._retained_parent_fds) >= 64:
        raise StorageDenied("TOO_MANY_PINNED_PATHS")
    parent = self._open_parent(parts[:-1], create=create_parents)
    try:
        if may_exist:
            try:
                leaf = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                if not stat.S_ISREG(leaf.st_mode) or leaf.st_dev != self._initial_dev:
                    raise StorageDenied("UNSAFE_EXISTING_LEAF")
        else:
            try:
                os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise StorageDenied("TARGET_ALREADY_EXISTS")
        self.verify()
        # Keep the exact parent inode open until the guard exits. Returning
        # /proc/self/fd/<ROOT>/nested/file after closing a verified parent
        # would allow a rename/symlink swap between verification and open.
        self._retained_parent_fds.append(parent)
        return f"/proc/self/fd/{parent}/{parts[-1]}"
    except BaseException:
        os.close(parent)
        raise



def _connect_sqlite(self: GuardedStorage, relative: str = "meteorhub.sqlite3", *,
                    create: bool = False, read_only: bool = False) -> sqlite3.Connection:
    """Atomically open SQLite's inode with O_NOFOLLOW before SQLite sees a path.

    SQLite connects through /proc/self/fd/<database-fd> rather than the
    swappable directory filename. It canonicalizes this handle, including its
    WAL/SHM siblings, as verified on Linux in isolated test cases. This does not
    protect against privileged kernel operations or another process with equal
    access deliberately replacing WAL/SHM files; deployment still needs
    exclusive writable directory ownership and a separate acceptance gate.
    """
    if create and read_only:
        raise StorageDenied('CONFLICTING_DATABASE_MODES')
    path = self.guarded_target(relative, may_exist=True)
    self.verify()
    flags = (os.O_RDONLY if read_only else os.O_RDWR) | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = None
    db = None
    try:
        if create:
            try:
                fd = os.open(path, flags | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                fd = os.open(path, flags)
        else:
            try:
                fd = os.open(path, flags)
            except FileNotFoundError as exc:
                raise StorageDenied('DATABASE_NOT_FOUND') from exc
        info = os.fstat(fd)
        leaf = os.stat(path, follow_symlinks=False)
        if (not stat.S_ISREG(info.st_mode) or info.st_dev != self._initial_dev
                or (info.st_dev, info.st_ino) != (leaf.st_dev, leaf.st_ino)):
            raise StorageDenied('UNSAFE_SQLITE_LEAF')
        self.verify()
        mode = 'ro' if read_only else 'rw'
        uri = f"file:/proc/self/fd/{fd}?mode={mode}"
        if read_only:
            db = sqlite3.connect(uri, uri=True, timeout=30)
        else:
            db = sqlite3.connect(uri, uri=True, timeout=30,
                                 factory=GuardedSQLiteConnection)
            db._mm01_guard = self
            db._mm01_pinned_path = path
            db._mm01_db_inode = info.st_dev, info.st_ino
        self.verify()
        self._sqlite_connections.append(db)
        self._sqlite_file_fds.append(fd)
        return db
    except BaseException as exc:
        if db is not None:
            db.close()
        if fd is not None:
            os.close(fd)
        if isinstance(exc, sqlite3.Error):
            raise StorageDenied('DATABASE_OPEN_FAILED') from exc
        raise


GuardedStorage.guarded_target = _guarded_target
GuardedStorage.connect_sqlite = _connect_sqlite


def _create_dir(self: GuardedStorage, relative: str) -> str:
    parts = _parts(relative)
    self.verify()
    if len(self._retained_parent_fds) >= 64:
        raise StorageDenied("TOO_MANY_PINNED_PATHS")
    parent = self._open_parent(parts[:-1], create=True)
    directory = None
    try:
        self.verify()
        try:
            os.mkdir(parts[-1], mode=0o700, dir_fd=parent)
        except FileExistsError:
            pass
        directory = os.open(parts[-1], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                            dir_fd=parent)
        if os.fstat(directory).st_dev != self._initial_dev:
            raise StorageDenied("NESTED_MOUNT_NOT_ALLOWED")
        os.fsync(parent)
        self.verify()
        # Holding this exact directory FD prevents later symlink swap when
        # the caller creates a report/file under the returned path.
        self._retained_parent_fds.append(directory)
        return f"/proc/self/fd/{directory}"
    except BaseException:
        if directory is not None:
            os.close(directory)
        raise
    finally:
        os.close(parent)


GuardedStorage.create_dir = _create_dir
