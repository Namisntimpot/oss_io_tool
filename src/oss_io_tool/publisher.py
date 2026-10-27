"""Bounded artifact publication queue; failures surface before completion."""
from __future__ import annotations
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
import logging
from typing import Callable, Optional
from . import Path, copy_file, submit_io

class ArtifactPublisher:
    """Publish node-local checkpoints to persistent storage in the background."""

    def __init__(self, max_pending: int = 2):
        if max_pending < 1:
            raise ValueError("max_pending must be at least 1")
        self._max_pending = max_pending
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="oss-io-publish",
        )
        self._pending: deque[Future[None]] = deque()
        self._inflight_trees: set[str] = set()
        self._published_tree_files: dict[str, tuple[int, int]] = {}
        self._closed = False

    def submit(
        self,
        staged_path: str | Path,
        destination_path: str | Path,
        on_published: Optional[Callable[[], None]] = None,
        remove_source: bool = True,
    ) -> None:
        if self._closed:
            raise RuntimeError("checkpoint publisher is already closed")
        self._reap_completed()
        while len(self._pending) >= self._max_pending:
            self._pending.popleft().result()
        future = submit_io(self._executor,
            self._publish_one,
            Path(staged_path),
            Path(destination_path),
            on_published,
            remove_source,
        )
        self._pending.append(future)

    def sync_tree_coalesced(
        self,
        source_dir: str | Path,
        destination_dir: str | Path,
    ) -> bool:
        if self._closed:
            raise RuntimeError("checkpoint publisher is already closed")
        self._reap_completed()
        source_key = str(Path(source_dir))
        if source_key in self._inflight_trees:
            return False
        while len(self._pending) >= self._max_pending:
            self._pending.popleft().result()
        self._inflight_trees.add(source_key)
        future = submit_io(self._executor,
            self._publish_tree,
            Path(source_dir),
            Path(destination_dir),
            source_key,
        )
        self._pending.append(future)
        return True

    def drain(self) -> None:
        if self._closed:
            return
        error = None
        try:
            while self._pending:
                try:
                    self._pending.popleft().result()
                except BaseException as exc:
                    error = error or exc
        finally:
            self._executor.shutdown(wait=True)
            self._closed = True
        if error is not None:
            raise error

    def _reap_completed(self) -> None:
        while self._pending and self._pending[0].done():
            self._pending.popleft().result()

    @staticmethod
    def _publish_one(
        staged_path: Path,
        destination_path: Path,
        on_published: Optional[Callable[[], None]],
        remove_source: bool,
    ) -> None:
        try:
            copy_file(staged_path, destination_path)
            if on_published is not None:
                on_published()
        except Exception:
            logging.getLogger(__name__).exception(
                'Artifact publication failed: %s -> %s; staged source retained',
                staged_path, destination_path)
            raise
        if remove_source:
            staged_path.unlink(missing_ok=True)

    def _publish_tree(
        self,
        source_dir: Path,
        destination_dir: Path,
        source_key: str,
    ) -> None:
        try:
            if not source_dir.exists():
                return
            for source_path in sorted(source_dir.rglob("*")):
                if not source_path.is_file() or ".tmp.upload." in source_path.name:
                    continue
                relative_path = source_path.relative_to(source_dir)
                signature = (source_path.stat().st_size, source_path.stat().st_mtime_ns)
                destination = destination_dir / relative_path
                key = str(destination)
                if self._published_tree_files.get(key) == signature:
                    continue
                self._publish_one(
                    source_path,
                    destination,
                    on_published=None,
                    remove_source=False,
                )
                self._published_tree_files[key] = signature
        finally:
            self._inflight_trees.discard(source_key)
