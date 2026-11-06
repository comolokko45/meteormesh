"""MM-01 staging integration for MeteorHub 0.9; no service management.

HDD writes are pinned to a verified filesystem via /proc/self/fd and guarded
SQLite checks. The guard is not an automatic approval for live deployment.
"""
from __future__ import annotations

import contextlib
import contextvars
import os
import sqlite3
import stat
from pathlib import Path
from typing import Iterator
from storage_guard import GuardedStorage, StorageDenied, StoragePolicy

ROOT = Path('/srv/meteor-data')
_ACTIVE: contextvars.ContextVar[GuardedStorage | None] = contextvars.ContextVar('mm01_guard', default=None)


def _relative(path: Path, root: Path) -> str:
    if not path.is_absolute() or any(p in ('.', '..') for p in path.parts):
        raise StorageDenied('ABSOLUTE_CANONICAL_PATH_REQUIRED')
    try:
        rel = path.relative_to(root)
    except ValueError as ex:
        raise StorageDenied('WRITE_OUTSIDE_PROTECTED_HDD') from ex
    if any(x in ('.','..') for x in rel.parts):
        raise StorageDenied('UNSAFE_PATH')
    return rel.as_posix()


def _pinned_subpath(guard: GuardedStorage, path: Path, root: Path,
                    held_fds: list[int], *, directory: bool = False) -> Path:
    rel = _relative(path, root)
    if rel == '.':
        if directory:
            return Path(f'/proc/self/fd/{guard._fd}')
        raise StorageDenied('OUTPUT_MUST_BE_A_FILE')
    parts = rel.split('/')
    # Every component opened by a dirfd using O_NOFOLLOW. The child directory
    # remains tied to its original filesystem even if renamed during an I/O.
    fd = guard._open_parent(parts if directory else parts[:-1], create=False)
    held_fds.append(fd)
    # These descriptors identify verified, pinned directories on the mount.
    pins = getattr(guard, '_mm01_pins', None)
    if pins is None:
        pins = {}
        guard._mm01_pins = pins
    pins[fd] = '/'.join(parts if directory else parts[:-1])
    return (Path(f'/proc/self/fd/{fd}') if directory
            else Path(f'/proc/self/fd/{fd}/{parts[-1]}'))


def _relative_active(path: Path, guard: GuardedStorage) -> str:
    root = Path(guard.policy.root)
    p = str(path)
    prefix = f'/proc/self/fd/{guard._fd}/'
    for fd, subdir in getattr(guard, '_mm01_pins', {}).items():
        base = f'/proc/self/fd/{fd}'
        if p == base:
            return subdir or '.'
        if p.startswith(base + '/'):
            tail = p[len(base)+1:]
            rel = (subdir + '/' + tail) if subdir else tail
            if any(x in ('', '.', '..') for x in rel.split('/')):
                raise StorageDenied('INVALID_ACTIVE_PATH')
            return rel
    if p == f'/proc/self/fd/{guard._fd}':
        return '.'
    if p.startswith(prefix):
        rel = p[len(prefix):]
    else:
        rel = _relative(path, root)
    if rel in ('', '.') or any(s in ('', '.', '..') for s in rel.split('/')):
        raise StorageDenied('INVALID_ACTIVE_PATH')
    return rel


def is_unsafe_symlink(path: str | Path) -> bool:
    p = Path(path)
    g = _ACTIVE.get()
    if g is not None:
        known = {g._fd, *getattr(g, '_mm01_pins', {}).keys()}
        if str(p) in {f'/proc/self/fd/{fd}' for fd in known}:
            g.verify()
            return False
    return p.is_symlink()


def verify_write_path(path: str | Path) -> None:
    """Verify exact filesystem while a guarded CLI is executing.

    Direct library calls under production root without an active guard deny;
    isolated unittest source paths keep their existing test-only semantics.
    """
    p = Path(path)
    guard = _ACTIVE.get()
    if guard is not None:
        _relative_active(p, guard)
        guard.verify()
    elif p == ROOT or ROOT in p.parents:
        raise StorageDenied('PRODUCTION_WRITE_WITHOUT_GUARD')


def guarded_db_connect(path: str | Path, *, timeout: float = 30, read_only: bool = False, create: bool = False) -> sqlite3.Connection:
    """Use GuardedSQLiteConnection when the production CLI is active."""
    p = Path(path)
    guard = _ACTIVE.get()
    if guard is not None:
        rel = _relative_active(p, guard)
        guard.verify()
        # Existing Hub create_database() is allowed to create a missing DB;
        # a read-only status CLI is not a production writer.
        return guard.connect_sqlite(rel, create=create, read_only=read_only)
    verify_write_path(p)
    if read_only:
        if not p.is_file() or p.is_symlink():
            raise StorageDenied('READ_ONLY_DATABASE_UNAVAILABLE')
        return sqlite3.connect('file:' + p.as_uri().removeprefix('file:') + '?mode=ro', uri=True, timeout=timeout)
    return sqlite3.connect(p, timeout=timeout)


def guarded_partial_open(path: Path, mode: str):
    """Open a .part for the existing downloader without symlink/SSD fallback.

    The guard owns a pinned directory FD; append requires a preexisting regular
    .part rather than silently creating an unrelated file after a race.
    Outside the production tree this preserves old offline unittest behavior.
    """
    if mode not in ('wb', 'ab') or not path.name.endswith('.mp4.part'):
        raise StorageDenied('INVALID_PARTIAL_OPEN')
    guard = _ACTIVE.get()
    if guard is None:
        verify_write_path(path)
        return path.open(mode)
    rel = _relative_active(path, guard)
    parts = rel.split('/')
    guard.verify()
    parent = guard._open_parent(parts[:-1], create=False)
    try:
        flags = os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC
        if mode == 'wb':
            flags |= os.O_CREAT | os.O_EXCL
        else:
            flags |= os.O_APPEND
        guard.verify()
        fd = os.open(parts[-1], flags, 0o600, dir_fd=parent)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_dev != guard._initial_dev:
                raise StorageDenied('UNSAFE_PARTIAL_FILE')
            guard.verify()
            return os.fdopen(fd, mode)
        except BaseException:
            os.close(fd)
            raise
    finally:
        os.close(parent)


@contextlib.contextmanager
def guarded_cli(args, *, mode: str, root: Path = ROOT,
                policy: StoragePolicy | None = None, source=None) -> Iterator[None]:
    """Protect write commands. Production root must be EXACTLY /srv/meteor-data.

    Scoped via ContextVar, no new state database or daemon. Does not allow any
    writes when mount/UUID mismatch at entry and rechecks at each protected edge.
    """
    policy = policy or StoragePolicy(root=str(root))
    # Production callers cannot re-target the mount guard through a custom
    # path or a custom policy. Only an explicitly injected evidence fixture
    # may use a different root in isolated tests; this is never an installed
    # service configuration.
    if source is None and (root != ROOT or policy.root != str(ROOT)):
        raise StorageDenied('PRODUCTION_MOUNT_POLICY_IS_FIXED')
    if Path(policy.root) != root:
        raise StorageDenied('MOUNT_POLICY_ROOT_MISMATCH')
    if mode not in {'hub-sync', 'morning-run', 'pipeline-write', 'pull', 'inventory-output', 'event-index', 'boundary', 'lab-output', 'review-label', 'agent-memory'}:
        raise StorageDenied('UNKNOWN_WRITE_MODE')
    with GuardedStorage(policy, source=source) as guard:
        token = _ACTIVE.set(guard)
        held_fds: list[int] = []
        try:
            if mode in {'hub-sync', 'morning-run', 'pipeline-write', 'event-index', 'boundary', 'review-label', 'agent-memory'}:
                path = getattr(args, 'data_dir', None)
                if not isinstance(path, Path) or path != root:
                    raise StorageDenied('DATA_ROOT_MUST_MATCH_VERIFIED_MOUNT')
                args.data_dir = _pinned_subpath(guard, path, root, held_fds, directory=True)
            if mode == 'boundary':
                review = getattr(args, 'review_dir', None)
                if review is not None:
                    args.review_dir = _pinned_subpath(guard, review, root, held_fds,
                                                      directory=True)
                output_dir = getattr(args, 'output_dir', None)
                if output_dir is not None:
                    args.output_dir = guarded_output_dir(output_dir)
            if mode == 'pull':
                path = getattr(args, 'destination', None)
                if not isinstance(path, Path):
                    raise StorageDenied('INVALID_DESTINATION')
                args.destination = _pinned_subpath(guard, path, root, held_fds, directory=True)
            if mode == 'event-index':
                target = getattr(args, 'output_dir', None)
                if not isinstance(target, Path):
                    raise StorageDenied('OUTPUT_DIRECTORY_REQUIRED')
                rel = _relative(target, root)
                args.output_dir = guarded_output_dir(target)
            out = getattr(args, 'output', None)
            if out is not None:
                if not isinstance(out, Path):
                    raise StorageDenied('INVALID_OUTPUT')
                args.output = _pinned_subpath(guard, out, root, held_fds)
            guard.verify()
            yield
            guard.verify()
        finally:
            _ACTIVE.reset(token)
            for fd in reversed(held_fds):
                os.close(fd)


# Additional production write edges, without a new worker or queue.
def guarded_output_dir(path: Path) -> Path:
    """Create/pin a directory only below the verified disk."""
    guard = _ACTIVE.get()
    if guard is None:
        verify_write_path(path)
        path.mkdir(parents=True, exist_ok=True)
        return path
    rel = _relative_active(path, guard)
    if rel == '.':
        guard.verify()
        return path
    # A /proc/self/fd directory already pinned by this guard is safe as-is.
    if str(path).startswith('/proc/self/fd/') and str(path) in {
            f'/proc/self/fd/{fd}' for fd in getattr(guard, '_mm01_pins', {})}:
        guard.verify()
        return path
    guard.verify()
    # A long morning session may analyze hundreds of segments from one
    # device. Reuse its already-pinned output directory instead of steadily
    # consuming file descriptors until the 64-pin safety limit is reached.
    for fd, prior in getattr(guard, '_mm01_pins', {}).items():
        if prior == rel and fd in guard._retained_parent_fds:
            if os.fstat(fd).st_dev != guard._initial_dev:
                raise StorageDenied('PINNED_DIRECTORY_DEVICE_CHANGED')
            return Path(f'/proc/self/fd/{fd}')
    pinned = guard.create_dir(rel)
    fd = int(pinned.rsplit('/', 1)[-1])
    if not hasattr(guard, '_mm01_pins'):
        guard._mm01_pins = {}
    guard._mm01_pins[fd] = rel
    return Path(pinned)


def guarded_ffprobe_run(run_command, argv: list[str], video: Path, *, timeout: int = 45):
    """Pass a pinned *input inode* to ffprobe (CLOEXEC fd paths otherwise fail).

    No shell, no raw SSD fallback, no opening unverified symlinks. The input
    descriptor is inherited explicitly only for the duration of ffprobe.
    """
    options = dict(capture_output=True, text=True, timeout=timeout, check=False)
    guard = _ACTIVE.get()
    if guard is None:
        return run_command(argv, **options)
    rel = _relative_active(video, guard)
    parts = rel.split('/')
    if not parts[-1].endswith('.mp4') or argv.count(str(video)) != 1:
        raise StorageDenied('INVALID_FFPROBE_INPUT')
    guard.verify()
    parent = guard._open_parent(parts[:-1], create=False)
    source_fd = None
    try:
        source_fd = os.open(parts[-1], os.O_RDONLY | os.O_NONBLOCK |
                            os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
        info = os.fstat(source_fd)
        if not stat.S_ISREG(info.st_mode) or info.st_dev != guard._initial_dev:
            raise StorageDenied('UNSAFE_FFPROBE_INPUT')
        command = list(argv)
        command[command.index(str(video))] = f'/proc/self/fd/{source_fd}'
        guard.verify()
        result = run_command(command, pass_fds=(source_fd,), **options)
        guard.verify()
        return result
    finally:
        if source_fd is not None:
            os.close(source_fd)
        os.close(parent)


def guarded_detector_run(run_command, argv: list[str], pending: Path,
                         repo: Path, timeout_seconds: int):
    """Pin detector output AND source MP4 across child process execution."""
    guard = _ACTIVE.get()
    standard = dict(cwd=str(repo), capture_output=True, text=True,
                    timeout=timeout_seconds, check=False)
    if guard is None:
        verify_write_path(pending)
        return run_command(argv, **standard)
    rel = _relative_active(pending, guard)
    parts = rel.split('/')
    if not parts[-1].endswith('.pending.json'):
        raise StorageDenied('INVALID_DETECTOR_STAGING_NAME')
    if (len(argv) < 5 or '--save-path' not in argv or
            argv.count('--save-path') != 1 or
            argv.index('--save-path') + 1 >= len(argv)):
        raise StorageDenied('INVALID_DETECTOR_ARGUMENTS')
    # The existing bounded worker places its input at argv[2]. Keep that
    # invariant and refuse an arbitrary new invocation layout.
    source_path = Path(argv[2])
    source_rel = _relative_active(source_path, guard)
    source_parts = source_rel.split('/')
    if not source_parts[-1].endswith('.mp4'):
        raise StorageDenied('INVALID_DETECTOR_VIDEO_INPUT')
    guard.verify()
    output_parent = guard._open_parent(parts[:-1], create=False)
    input_parent = None
    input_fd = None
    try:
        input_parent = guard._open_parent(source_parts[:-1], create=False)
        input_fd = os.open(source_parts[-1], os.O_RDONLY | os.O_NONBLOCK |
                           os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=input_parent)
        info = os.fstat(input_fd)
        if not stat.S_ISREG(info.st_mode) or info.st_dev != guard._initial_dev:
            raise StorageDenied('UNSAFE_DETECTOR_VIDEO_INPUT')
        new_argv = list(argv)
        new_argv[2] = f'/proc/self/fd/{input_fd}'
        new_argv[new_argv.index('--save-path') + 1] = (
            f'/proc/self/fd/{output_parent}/{parts[-1]}')
        guard.verify()
        result = run_command(new_argv, pass_fds=(input_fd, output_parent), **standard)
        guard.verify()
        return result
    finally:
        if input_fd is not None:
            os.close(input_fd)
        if input_parent is not None:
            os.close(input_parent)
        os.close(output_parent)


def guarded_publish(src: Path, dst: Path) -> None:
    """Publish only a new regular file on the same pinned HDD. No replace."""
    guard = _ACTIVE.get()
    if guard is None:
        verify_write_path(src)
        verify_write_path(dst)
        if dst.exists() or dst.is_symlink():
            raise StorageDenied('DESTINATION_ALREADY_EXISTS')
        os.replace(src, dst)
        return
    source = _relative_active(src, guard).split('/')
    target = _relative_active(dst, guard).split('/')
    if source[:-1] != target[:-1]:
        raise StorageDenied('CROSS_DIRECTORY_PUBLISH_NOT_ALLOWED')
    guard.verify()
    parent = guard._open_parent(source[:-1], create=False)
    source_fd = None
    try:
        # Open and retain the *inode*, not just its name. An attacker/process
        # can replace a checked directory entry before a later os.link(name).
        # O_NONBLOCK avoids hanging on an unexpected FIFO before fstat rejects
        # it; O_NOFOLLOW refuses a symlink at open time.
        source_fd = os.open(source[-1], os.O_RDONLY | os.O_NOFOLLOW |
                            os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent)
        st = os.fstat(source_fd)
        if not stat.S_ISREG(st.st_mode) or st.st_dev != guard._initial_dev:
            raise StorageDenied('UNSAFE_PUBLISH_SOURCE')
        guard.verify()
        os.fsync(source_fd)
        # linkat(AT_SYMLINK_FOLLOW) follows the procfd magic link to the
        # pinned inode, NOT the mutable source name. src_dir_fd forces Python
        # to use linkat; this is Linux-specific and fails closed if unsupported.
        os.link(f'/proc/self/fd/{source_fd}', target[-1],
                src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=True)
        guard.verify()
        os.fsync(parent)
        # Never unlink a different file if the original name was swapped.
        try:
            current = os.stat(source[-1], dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError as exc:
            raise StorageDenied('PUBLISH_SOURCE_NAME_CHANGED') from exc
        if (current.st_dev, current.st_ino) != (st.st_dev, st.st_ino):
            raise StorageDenied('PUBLISH_SOURCE_NAME_CHANGED')
        os.unlink(source[-1], dir_fd=parent)
        os.fsync(parent)
        guard.verify()
    finally:
        if source_fd is not None:
            os.close(source_fd)
        os.close(parent)


def persistent_data_path(path: Path) -> Path:
    """Record a durable /srv path, NEVER the transient /proc/self/fd handle.

    Used for database/JSON metadata only. It grants no file access and cannot
    replace FD-pinned reads/writes. The active guard validates the mount first.
    """
    guard = _ACTIVE.get()
    if guard is None:
        if str(path).startswith('/proc/self/fd/'):
            raise StorageDenied('PERSISTENT_PATH_REQUIRES_ACTIVE_GUARD')
        return path
    rel = _relative_active(path, guard)
    guard.verify()
    return Path(guard.policy.root) / rel


def is_protected_write_context() -> bool:
    return _ACTIVE.get() is not None


def guarded_subprocess_output(run_command, argv: list[str], output: Path, *,
                              timeout: int, cwd: Path | None = None):
    """Run only a known, bounded command with its output in a pinned HDD dir.

    Keeps original test fixture calls unchanged outside production context.
    The caller supplies the already fixed ffmpeg/MetDetPy command, never shell.
    """
    kwargs = dict(capture_output=True, text=True, timeout=timeout, check=False)
    if cwd is not None:
        kwargs['cwd'] = str(cwd)
    guard = _ACTIVE.get()
    if guard is None:
        verify_write_path(output)
        return run_command(argv, **kwargs)
    rel = _relative_active(output, guard)
    leaf = rel.split('/')[-1]
    if not leaf.endswith(('.json', '.mp4', '.jpg')):
        raise StorageDenied('UNSUPPORTED_SUBPROCESS_OUTPUT')
    guard.verify()
    parent = guard._open_parent(rel.split('/')[:-1], create=False)
    try:
        new_argv = [str(x) for x in argv]
        wanted = str(output)
        if new_argv.count(wanted) != 1:
            raise StorageDenied('SUBPROCESS_OUTPUT_NOT_UNIQUE')
        index = new_argv.index(wanted)
        new_argv[index] = f'/proc/self/fd/{parent}/{leaf}'
        guard.verify()
        result = run_command(new_argv, pass_fds=(parent,), **kwargs)
        guard.verify()
        return result
    finally:
        os.close(parent)
