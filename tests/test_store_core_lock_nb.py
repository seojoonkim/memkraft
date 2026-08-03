"""Slice 2 — bounded non-blocking exclusive lock in ``store_core``.

``_lock_current_inode`` gains an optional ``timeout_s``: when the store lock
is already held, it gives up with :class:`store_core.StoreBusy` instead of
queueing forever. The default (``timeout_s=None``) must keep the exact prior
blocking behavior, including inode revalidation after ``os.replace``.
"""

import os
import threading
import time

import pytest

from memkraft import store_core

fcntl = pytest.importorskip("fcntl")


def _hold_lock(path):
    """Take an exclusive flock on ``path`` via an independent descriptor."""
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def _release(fd):
    fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)


def test_timeout_raises_store_busy_within_200ms(tmp_path):
    store = tmp_path / "store.jsonl"
    held = _hold_lock(store)
    try:
        start = time.monotonic()
        with pytest.raises(store_core.StoreBusy):
            store_core._lock_current_inode(
                str(store), os.O_RDWR | os.O_CREAT, timeout_s=0.1
            )
        elapsed = time.monotonic() - start
    finally:
        _release(held)
    assert elapsed < 0.2


def test_store_busy_carries_the_store_path(tmp_path):
    store = tmp_path / "store.jsonl"
    held = _hold_lock(store)
    try:
        with pytest.raises(store_core.StoreBusy) as excinfo:
            store_core._lock_current_inode(
                str(store), os.O_RDWR | os.O_CREAT, timeout_s=0.05
            )
    finally:
        _release(held)
    assert str(store) in str(excinfo.value)


def test_timeout_none_still_blocks_until_the_holder_releases(tmp_path):
    store = tmp_path / "store.jsonl"
    held = _hold_lock(store)
    released_at = []

    def release_later():
        time.sleep(0.3)
        released_at.append(time.monotonic())
        _release(held)

    releaser = threading.Thread(target=release_later)
    releaser.start()
    try:
        fd = store_core._lock_current_inode(str(store), os.O_RDWR | os.O_CREAT)
        acquired_at = time.monotonic()
    finally:
        releaser.join()
    try:
        assert acquired_at >= released_at[0]
    finally:
        store_core._unlock(fd)
        os.close(fd)


def test_no_contention_acquires_immediately_with_a_timeout(tmp_path):
    store = tmp_path / "store.jsonl"
    fd = store_core._lock_current_inode(
        str(store), os.O_RDWR | os.O_CREAT, timeout_s=0.1
    )
    try:
        assert os.fstat(fd).st_ino == os.stat(str(store)).st_ino
    finally:
        store_core._unlock(fd)
        os.close(fd)


def test_inode_revalidation_after_replacement(tmp_path):
    """A locker that queued on the old inode must end up on the new one."""
    store = tmp_path / "store.jsonl"
    store.write_text("old\n", encoding="utf-8")
    old_ino = store.stat().st_ino
    held = _hold_lock(store)

    def replace_later():
        time.sleep(0.2)
        replacement = tmp_path / "store.new"
        replacement.write_text("new\n", encoding="utf-8")
        os.replace(str(replacement), str(store))
        _release(held)

    swapper = threading.Thread(target=replace_later)
    swapper.start()
    try:
        fd = store_core._lock_current_inode(str(store), os.O_RDWR | os.O_CREAT)
    finally:
        swapper.join()
    try:
        st_fd = os.fstat(fd)
        assert st_fd.st_ino == store.stat().st_ino
        assert st_fd.st_ino != old_ino
    finally:
        store_core._unlock(fd)
        os.close(fd)


def test_inode_revalidation_still_applies_under_a_timeout(tmp_path):
    store = tmp_path / "store.jsonl"
    store.write_text("old\n", encoding="utf-8")
    old_ino = store.stat().st_ino
    replacement = tmp_path / "store.new"
    replacement.write_text("new\n", encoding="utf-8")
    os.replace(str(replacement), str(store))

    fd = store_core._lock_current_inode(
        str(store), os.O_RDWR | os.O_CREAT, timeout_s=0.5
    )
    try:
        st_fd = os.fstat(fd)
        assert st_fd.st_ino == store.stat().st_ino
        assert st_fd.st_ino != old_ino
    finally:
        store_core._unlock(fd)
        os.close(fd)
