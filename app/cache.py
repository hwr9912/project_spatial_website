# app/cache.py
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Tuple, Optional

from . import config as cfg


@dataclass
class CachePruneResult:
    before_bytes: int
    after_bytes: int
    deleted_files: int
    deleted_bytes: int
    elapsed_ms: int
    skipped_due_to_lock: bool = False


def _iter_files(dirs: Iterable[Path]) -> Iterable[Path]:
    for d in dirs:
        if not d.exists():
            continue
        for p in d.rglob("*"):
            if p.is_file():
                yield p


def _total_size_bytes(dirs: Iterable[Path]) -> int:
    total = 0
    for p in _iter_files(dirs):
        try:
            total += p.stat().st_size
        except FileNotFoundError:
            # 并发情况下文件可能刚被删
            pass
    return total


# -------- prune lock (global) --------
def _acquire_prune_lock(lock_path: Path) -> Optional[int]:
    """
    Non-blocking lock. Returns fd if acquired, else None.
    """
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
        os.write(fd, f"pid={os.getpid()} time={time.time()}\n".encode("utf-8"))
        return fd
    except FileExistsError:
        return None


def _release_prune_lock(fd: int, lock_path: Path) -> None:
    try:
        os.close(fd)
    finally:
        try:
            lock_path.unlink(missing_ok=True)
        except Exception:
            pass


def prune_cache_if_needed(
    max_bytes: Optional[int] = None,
    dirs: Optional[Iterable[Path]] = None,
    keep_newest: int = 0,
) -> CachePruneResult:
    """
    若缓存目录总大小超过 max_bytes，则按 mtime 从旧到新删除文件，直到不超过 max_bytes。

    参数：
    - max_bytes: 最大允许总字节数，默认 cfg.CACHE_MAX_BYTES
    - dirs: 要纳入配额的目录集合，默认 cfg.CACHE_DIRS（png/pdf/tiff）
    - keep_newest: 可选，保留最新 N 个文件不删（默认 0）

    并发策略：
    - 使用一个全局 prune lock，避免多个请求同时大量删除。
    - 如果拿不到锁：直接跳过（不阻塞），返回 skipped_due_to_lock=True。
    """
    t0 = time.time()
    if max_bytes is None:
        max_bytes = getattr(cfg, "CACHE_MAX_BYTES", 10 * 1024**3)
    if dirs is None:
        dirs = getattr(cfg, "CACHE_DIRS", [cfg.PNG_DIR, cfg.PDF_DIR, cfg.TIFF_DIR])

    # 先算一遍大小（没超就直接返回）
    before = _total_size_bytes(dirs)
    if before <= max_bytes:
        return CachePruneResult(before, before, 0, 0, int((time.time() - t0) * 1000), False)

    # 全局清理锁（不阻塞）
    cfg.LOCK_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = cfg.LOCK_DIR / "_prune.lock"
    fd = _acquire_prune_lock(lock_path)
    if fd is None:
        # 别人正在清理，跳过即可
        now_bytes = _total_size_bytes(dirs)
        return CachePruneResult(
            before_bytes=before,
            after_bytes=now_bytes,
            deleted_files=0,
            deleted_bytes=0,
            elapsed_ms=int((time.time() - t0) * 1000),
            skipped_due_to_lock=True,
        )

    deleted_files = 0
    deleted_bytes = 0

    try:
        # 重新计算一次（拿到锁后可能已经被别人清理过）
        current = _total_size_bytes(dirs)
        if current <= max_bytes:
            return CachePruneResult(before, current, 0, 0, int((time.time() - t0) * 1000), False)

        # 收集 (mtime, size, path)
        entries: List[Tuple[float, int, Path]] = []
        for p in _iter_files(dirs):
            try:
                st = p.stat()
                entries.append((st.st_mtime, st.st_size, p))
            except FileNotFoundError:
                pass

        # 从旧到新
        entries.sort(key=lambda x: x[0])

        if keep_newest > 0 and len(entries) > keep_newest:
            deletable = entries[:-keep_newest]
        else:
            deletable = entries

        for _, size, path in deletable:
            if current <= max_bytes:
                break
            try:
                path.unlink(missing_ok=True)
                deleted_files += 1
                deleted_bytes += size
                current -= size
            except Exception:
                # 权限/占用等删不掉就跳过
                continue

        after = _total_size_bytes(dirs)
        return CachePruneResult(
            before_bytes=before,
            after_bytes=after,
            deleted_files=deleted_files,
            deleted_bytes=deleted_bytes,
            elapsed_ms=int((time.time() - t0) * 1000),
            skipped_due_to_lock=False,
        )
    finally:
        _release_prune_lock(fd, lock_path)
