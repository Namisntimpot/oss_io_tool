"""Synchronous reads and optional asynchronous writes for local, OSS and S3.

Cloud SDKs are imported lazily. The core requires only the Python standard library.
``IO`` owns its uploads; ``flush()`` is a durability barrier and reports errors.
``Path`` preserves cloud schemes while offering the subset of pathlib used by
local and object storage. File-only libraries can use ``memory_file`` on Linux.
"""
from __future__ import annotations

import atexit
import configparser
import hashlib
import io
import os
import posixpath
import threading
import tempfile
import weakref
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager, ExitStack
from contextvars import ContextVar, copy_context
from functools import wraps
from pathlib import Path as LocalPath, PurePosixPath
from types import SimpleNamespace
from urllib.parse import unquote

from ._oss_upload import upload as _oss_upload

__version__ = "0.1.0"


def _remote(path):
    return os.fspath(path).startswith(("oss://", "s3://"))


def _oss_parts(path):
    bucket, _, key = os.fspath(path)[6:].partition("/")
    if not bucket:
        raise ValueError(f"missing OSS bucket: {path}")
    return bucket, key


def _missing_oss_key(exc):
    """The v2 SDK wraps service errors in OperationError."""
    error = exc.unwrap() if hasattr(exc, "unwrap") else exc
    return getattr(error, "code", None) == "NoSuchKey"


def _glob_matches(name, pattern):
    """Glob segments are anchored; only ** may span directory separators."""
    import fnmatch
    names, patterns = name.split("/"), pattern.split("/")

    def match(ni, pi):
        if pi == len(patterns):
            return ni == len(names)
        if patterns[pi] == "**":
            return match(ni, pi + 1) or (ni < len(names) and match(ni + 1, pi))
        return (ni < len(names) and fnmatch.fnmatchcase(names[ni], patterns[pi])
                and match(ni + 1, pi + 1))

    return match(0, 0)


class RemoteListingError(RuntimeError):
    """Runtime cloud directory discovery is forbidden; consume asset indexes."""


_instances = weakref.WeakSet()


class IO:
    """Backend-independent byte/text IO. Reads always finish before returning.

    ``write_bytes(..., asynchronous=True)`` snapshots the supplied content,
    schedules a write and returns a Future immediately. Sync writes return None
    after persistence. Writes to the same path are ordered, including sync writes
    after queued writes. Use a context manager or flush before completion markers.
    """

    def __init__(self, *, workers=4, oss_client=None, cache_reads=False,
                 seed_content=None, record_writes=False, write_sink=None, max_remote_read_bytes=None):
        if not isinstance(workers, int) or workers < 1:
            raise ValueError("workers must be a positive integer")
        self._workers = workers
        self._pool = None
        self._oss_client = oss_client
        self._owns_oss_client = oss_client is None
        _instances.add(self)
        self._lock = threading.RLock()
        self._pending = []
        self._tails = {}
        self._closed = False
        self.bytes_read = 0
        self.bytes_written = 0
        self.remote_bytes_read = 0
        self.cache_hits = 0
        self._cache_reads = bool(cache_reads)
        self._seed_content = dict(seed_content or {})
        self.memory_cache = {}
        self.written_objects = {}
        self._record_writes = record_writes
        self._write_sink = write_sink
        self._read_budget = max_remote_read_bytes

    def _oss(self):
        with self._lock:
            if self._oss_client is None:
                import alibabacloud_oss_v2 as oss
                parser = configparser.ConfigParser()
                parser.read(os.path.expanduser(os.getenv(
                    "OSS_CONFIG_FILE", "~/.ossutilconfig")))
                section = next((s for s in parser.sections() if s.lower() == "credentials"),
                               "default" if parser.has_section("default") else None)
                settings = parser[section] if section else {}
                ak = (os.getenv("OSS_ACCESS_KEY_ID") or os.getenv("ALIBABA_CLOUD_ACCESS_KEY_ID")
                      or settings.get("accesskeyid"))
                sk = (os.getenv("OSS_ACCESS_KEY_SECRET") or os.getenv("ALIBABA_CLOUD_ACCESS_KEY_SECRET")
                      or settings.get("accesskeysecret"))
                if not ak or not sk:
                    raise ValueError("OSS credentials missing: configure OSS_ACCESS_KEY_ID/"
                                     "OSS_ACCESS_KEY_SECRET or ~/.ossutilconfig")
                cfg = oss.config.load_default()
                attempts = os.getenv('OSS_IO_RETRY_MAX_ATTEMPTS', os.getenv('NSL_OSS_RETRY_MAX_ATTEMPTS'))
                if attempts is not None:
                    cfg.retry_max_attempts = int(attempts)
                    if cfg.retry_max_attempts < 1:
                        raise ValueError('positive OSS_IO_RETRY_MAX_ATTEMPTS required')
                cfg.region = os.getenv("OSS_REGION") or settings.get("region")
                cfg.endpoint = os.getenv("OSS_ENDPOINT") or settings.get("endpoint") or cfg.endpoint
                if not cfg.region:
                    raise ValueError("OSS region missing: configure OSS_REGION or ~/.ossutilconfig")
                cfg.credentials_provider = oss.credentials.StaticCredentialsProvider(
                    ak, sk, os.getenv("OSS_SESSION_TOKEN") or settings.get("stsToken".lower()))
                self._oss_client = oss.Client(cfg)
            return self._oss_client

    def read_bytes(self, path):
        path = os.fspath(path)
        if self._write_sink is not None and self._write_sink.matches(path):
            data = self._write_sink.read(path)
            with self._lock:
                self.bytes_read += len(data)
            return data
        cache_key = None
        if _remote(path) and (self._cache_reads or self._read_budget is not None):
            stat = self.stat(path)
            if self._cache_reads:
                version = getattr(stat, "etag", None)
                cache_key = ("object", path, stat.st_size, stat.st_mtime_ns, version)
                with self._lock:
                    data = self.memory_cache.get(cache_key)
                if data is None and version:
                    # An explicit in-memory seed may avoid downloading immutable
                    # test inputs again. No local cache file is opened or created.
                    seeded = self._seed_content.get(version.strip('"').lower())
                    if seeded is not None and len(seeded) == stat.st_size:
                        data = seeded
                if data is not None:
                    with self._lock:
                        self.memory_cache[cache_key] = data
                        self.cache_hits += 1
                        self.bytes_read += len(data)
                    return data
            with self._lock:
                if self._read_budget is not None and self.remote_bytes_read + stat.st_size > self._read_budget:
                    raise RuntimeError(f"remote read budget exceeded before downloading {path}")
                # Reserve before downloading so concurrent reads respect the cap.
                self.remote_bytes_read += stat.st_size
        if path.startswith("oss://"):
            import alibabacloud_oss_v2 as oss
            bucket, key = _oss_parts(path)
            try:
                result = self._oss().get_object(oss.GetObjectRequest(bucket=bucket, key=key))
            except (oss.exceptions.ServiceError, oss.exceptions.OperationError) as exc:
                if _missing_oss_key(exc):
                    raise FileNotFoundError(path) from exc
                raise
            try:
                data = result.body.read()
            finally:
                result.body.close()
        elif path.startswith("s3://"):
            import megfile
            with megfile.smart_open(path, "rb") as stream:
                data = stream.read()
        else:
            data = LocalPath(path).read_bytes()
        with self._lock:
            self.bytes_read += len(data)
            if _remote(path) and not self._cache_reads and self._read_budget is None:
                self.remote_bytes_read += len(data)
            if cache_key is not None:
                self.memory_cache[cache_key] = data
        return data

    def read_text(self, path, encoding="utf-8"):
        return self.read_bytes(path).decode(encoding)

    def read_range(self, path, start, length):
        """Read one indexed uncompressed-tar member without downloading the tar."""
        path = os.fspath(path)
        if type(start) is not int or type(length) is not int or start < 0 or length <= 0:
            raise ValueError("range start/length must be nonnegative/positive integers")
        remote = _remote(path)
        stat = self.stat(path)
        if start + length > stat.st_size:
            raise ValueError(f"indexed byte range exceeds object size: {path}")
        version = getattr(stat, "etag", None)
        key = ("range", path, start, length, version, stat.st_mtime_ns)
        with self._lock:
            cached = self.memory_cache.get(key) if self._cache_reads else None
            if cached is not None:
                self.cache_hits += 1
                self.bytes_read += len(cached)
                return cached
            if remote:
                if self._read_budget is not None and self.remote_bytes_read + length > self._read_budget:
                    raise RuntimeError(f"remote read budget exceeded before range download: {path}")
                self.remote_bytes_read += length
        if path.startswith("oss://"):
            import alibabacloud_oss_v2 as oss
            bucket, obj = _oss_parts(path)
            result = self._oss().get_object(oss.GetObjectRequest(bucket=bucket, key=obj,
                range_header=f"bytes={start}-{start+length-1}", if_match=version))
            try:
                if result.content_length != length:
                    raise RuntimeError(f"OSS did not honor indexed byte range: {path}")
                data = result.body.read()
            finally:
                result.body.close()
        elif path.startswith("s3://"):
            import megfile
            with megfile.smart_open(path, "rb") as stream:
                stream.seek(start)
                data = stream.read(length)
        else:
            with LocalPath(path).open("rb") as stream:
                stream.seek(start)
                data = stream.read(length)
        if len(data) != length:
            raise RuntimeError(f"range response length mismatch: {path}")
        with self._lock:
            self.bytes_read += len(data)
            if self._cache_reads:
                self.memory_cache[key] = data
        return data

    def _put(self, path, data):
        if self._write_sink is not None and self._write_sink.matches(path):
            self._write_sink.write(path,data)
        elif path.startswith("oss://"):
            import alibabacloud_oss_v2 as oss
            bucket, key = _oss_parts(path)
            _oss_upload(self._oss(), oss.PutObjectRequest(bucket=bucket, key=key), data, len(data))
        elif path.startswith("s3://"):
            import megfile
            with megfile.smart_open(path, "wb") as stream:
                stream.write(data)
        else:
            p = LocalPath(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix=p.name + ".tmp.", dir=p.parent)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
                os.replace(temporary, p)
            finally:
                LocalPath(temporary).unlink(missing_ok=True)
        with self._lock:
            self.bytes_written += len(data)
            if self._record_writes:
                self.written_objects[path] = {"size": len(data), "md5": hashlib.md5(data).hexdigest()}

    def write_bytes(self, path, data, *, asynchronous=False):
        path, data = os.fspath(path), bytes(data)
        return self._ordered_write(path, lambda: self._put(path, data), asynchronous=asynchronous)

    def _ordered_write(self, path, operation, *, asynchronous=False):
        """Serialize all publication methods targeting the same path in this IO."""
        path = os.fspath(path)

        def perform():
            try:
                if previous is not None:
                    previous.result()
                operation()
            except BaseException as exc:
                completion.set_exception(exc)
                if not asynchronous:
                    raise
            else:
                completion.set_result(None)
            finally:
                with self._lock:
                    if self._tails.get(path) is completion:
                        del self._tails[path]

        # Reservation and submission share a lock: concurrent same-key uploads
        # must reach the executor in dependency order to avoid pool starvation.
        with self._lock:
            if self._closed:
                raise RuntimeError("IO is closed")
            previous = self._tails.get(path)
            completion = Future()
            completion.set_running_or_notify_cancel()
            self._tails[path] = completion
            if asynchronous:
                if self._pool is None:
                    self._pool = ThreadPoolExecutor(max_workers=self._workers,
                                                    thread_name_prefix="oss-io-upload")
                self._pending.append(completion)
                self._pool.submit(perform)
        if asynchronous:
            return completion
        perform()
        return None

    def write_text(self, path, data, encoding="utf-8", *, asynchronous=False):
        return self.write_bytes(path, data.encode(encoding), asynchronous=asynchronous)

    def remove(self, path, *, missing_ok=True):
        path = os.fspath(path)
        if path.startswith("oss://"):
            import alibabacloud_oss_v2 as oss
            bucket,key = _oss_parts(path)
            self._oss().delete_object(oss.DeleteObjectRequest(bucket=bucket,key=key))
        elif path.startswith("s3://"):
            import megfile
            megfile.smart_remove(path,missing_ok=missing_ok)
        else:
            LocalPath(path).unlink(missing_ok=missing_ok)

    def flush(self):
        """Wait for queued uploads, collecting all results before raising."""
        error = None
        while True:
            with self._lock:
                pending, self._pending = self._pending, []
            if not pending:
                break
            for future in pending:
                try:
                    future.result()
                except BaseException as exc:
                    error = error or exc
        if error is not None:
            raise error

    def close(self):
        with self._lock:
            self._closed = True
            pool = self._pool
        try:
            self.flush()
        finally:
            try:
                if pool is not None:
                    pool.shutdown(wait=True)
            finally:
                self.memory_cache.clear()
                self._seed_content.clear()
                self.written_objects.clear()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def stat(self, path):
        path = os.fspath(path)
        if self._write_sink is not None and self._write_sink.matches(path):
            result = self._write_sink.info(path)
            return SimpleNamespace(st_size=result["size"],st_mtime_ns=0,etag=result.get("md5"))
        if path.startswith("oss://"):
            import alibabacloud_oss_v2 as oss
            bucket, key = _oss_parts(path)
            try:
                result = self._oss().head_object(oss.HeadObjectRequest(bucket=bucket, key=key))
            except (oss.exceptions.ServiceError, oss.exceptions.OperationError) as exc:
                if _missing_oss_key(exc):
                    raise FileNotFoundError(path) from exc
                raise
            return SimpleNamespace(st_size=result.content_length,
                                   st_mtime_ns=int(result.last_modified.timestamp() * 1e9),
                                   etag=getattr(result, "etag", None),
                                   metadata=getattr(result, "metadata", None) or {})
        if path.startswith("s3://"):
            import megfile
            st = megfile.smart_stat(path)
            return SimpleNamespace(st_size=st.size, st_mtime_ns=int(st.mtime * 1e9),
                                   etag=(getattr(st, "extra", None) or {}).get("ETag"))
        return LocalPath(path).stat()

    def exists(self, path):
        path = os.fspath(path)
        if self._write_sink is not None and self._write_sink.matches(path):
            return self._write_sink.exists(path)
        if not _remote(path):
            return LocalPath(path).exists()
        try:
            self.stat(path)
            return True
        except FileNotFoundError:
            return False

    def _oss_list(self, path, *, recursive=False, limit=None):
        raise RemoteListingError(f"cloud listing disabled: {path}; use a prebuilt index")
        yield  # Keep this private API an iterator, without issuing an SDK call.

    def inventory(self, path, *, allow_remote=False):
        """Explicit administrative snapshot; normal cloud glob/iterdir stay disabled.

        Planners may opt in once to publish an immutable object inventory. Data
        workers must consume that inventory rather than discover directories.
        """
        path = os.fspath(path)
        path = path.rstrip('/') if _remote(path) else path
        if path.startswith('oss://'):
            if not allow_remote:
                raise RemoteListingError('explicit allow_remote required for inventory: ' + path)
            import alibabacloud_oss_v2 as oss
            bucket, key = _oss_parts(path)
            token = None
            while True:
                result = self._oss().list_objects_v2(oss.ListObjectsV2Request(
                    bucket=bucket, prefix=key.rstrip('/') + '/' if key else '', max_keys=1000,
                    continuation_token=token))
                for obj in result.contents or []:
                    if not obj.key.endswith('/'):
                        yield {'path': 'oss://' + bucket + '/' + obj.key, 'size': int(obj.size),
                               'etag': obj.etag, 'mtime_ns': int(obj.last_modified.timestamp() * 1e9)}
                if not result.is_truncated:
                    break
                next_token = result.next_continuation_token
                if not next_token or next_token == token:
                    raise RuntimeError('inventory pagination did not advance')
                token = next_token
        elif path.startswith('s3://'):
            raise RemoteListingError('S3 administrative inventory is not implemented')
        else:
            for target in sorted(LocalPath(path).rglob('*')):
                if target.is_file():
                    stat = target.stat()
                    yield {'path': str(target), 'size': stat.st_size,
                           'etag': None, 'mtime_ns': stat.st_mtime_ns}

    def is_dir(self, path):
        path = os.fspath(path)
        if self._write_sink is not None and self._write_sink.matches(path):
            return self._write_sink.info(path).get("directory",False) if self._write_sink.exists(path) else False
        if _remote(path):
            raise RemoteListingError(f"cloud directory probe disabled: {path}; use a prebuilt index")
        return LocalPath(path).is_dir()

    def iterdir(self, path):
        path = os.fspath(path)
        if self._write_sink is not None and self._write_sink.matches(path):
            for name,directory in self._write_sink.listing(path):
                yield Path(name,_directory=directory)
        elif _remote(path):
            raise RemoteListingError(f"cloud listing disabled: {path}; use a prebuilt index")
        else:
            for p in LocalPath(path).iterdir():
                yield Path(p)

    def glob(self, path, pattern):
        path = os.fspath(path)
        if self._write_sink is not None and self._write_sink.matches(path):
            for name,directory in self._write_sink.listing(path,recursive=True):
                if _glob_matches(str(Path(name).relative_to(path)),pattern):
                    yield Path(name,_directory=directory)
            return
        if not _remote(path):
            yield from (Path(p) for p in LocalPath(path).glob(pattern))
            return
        raise RemoteListingError(f"cloud glob disabled: {path}/{pattern}; use a prebuilt index")


_default_budget = os.getenv("OSS_IO_READ_BUDGET_BYTES", os.getenv("NSL_IO_READ_BUDGET_BYTES"))
default_io = IO(cache_reads=os.getenv("OSS_IO_MEMORY_CACHE", os.getenv("NSL_IO_MEMORY_CACHE", "0")) == "1",
                max_remote_read_bytes=int(_default_budget) if _default_budget else None)
_io_session = ContextVar("oss_io_session", default=None)


def current_io():
    return _io_session.get() or default_io


@contextmanager
def io_session(**kwargs):
    """Scope all module/Path/asset IO and in-memory read reuse to a with block."""
    with IO(**kwargs) as client:
        token = _io_session.set(client)
        try:
            yield client
        finally:
            _io_session.reset(token)


def read_bytes(path):
    return current_io().read_bytes(path)


def read_text(path, encoding="utf-8"):
    return current_io().read_text(path, encoding)


def write_bytes(path, data, *, asynchronous=False):
    return current_io().write_bytes(path, data, asynchronous=asynchronous)


def write_text(path, data, encoding="utf-8", *, asynchronous=False):
    return current_io().write_text(path, data, encoding, asynchronous=asynchronous)


def flush():
    return current_io().flush()
atexit.register(default_io.close)


async def async_read_bytes(path):
    """Read without blocking the asyncio event loop; preserve the IO session."""
    import asyncio
    return await asyncio.to_thread(read_bytes, path)


async def async_read_text(path, encoding="utf-8"):
    return (await async_read_bytes(path)).decode(encoding)


async def async_write_bytes(path, data):
    """Snapshot, enqueue and await publication; flush still tracks cancellation."""
    import asyncio
    return await asyncio.wrap_future(write_bytes(path, data, asynchronous=True))


async def async_write_text(path, data, encoding="utf-8"):
    return await async_write_bytes(path, data.encode(encoding))


async def async_flush():
    import asyncio
    return await asyncio.to_thread(flush)


class Path(os.PathLike):
    """URI-preserving path operations for local files, OSS and S3."""

    def __init__(self, *parts, _directory=None):
        raw = os.fspath(parts[0]) if parts else "."
        for part in parts[1:]:
            other = os.fspath(part)
            raw = other if _remote(other) or other.startswith("/") else raw.rstrip("/") + "/" + other
        if _remote(raw):
            scheme, rest = raw.split("://", 1)
            bucket, _, key = rest.partition("/")
            raw = scheme + "://" + bucket + ("/" + posixpath.normpath(key) if key else "")
        else:
            raw = str(LocalPath(raw))
        self._path = raw
        self._directory = _directory

    def __str__(self):
        return self._path

    def __fspath__(self):
        return self._path

    def __repr__(self):
        return f"Path({self._path!r})"

    def __hash__(self):
        return hash(self._path)

    def __eq__(self, other):
        if isinstance(other, (str, os.PathLike)):
            return self._path == str(Path(other))
        return NotImplemented

    def __lt__(self, other):
        return self._path < os.fspath(other)

    def __truediv__(self, other):
        return Path(self, other)

    def __rtruediv__(self, other):
        return Path(other, self)

    @property
    def name(self):
        return self._path.rstrip("/").rsplit("/", 1)[-1]

    @property
    def suffix(self):
        return PurePosixPath(self.name).suffix

    @property
    def stem(self):
        return PurePosixPath(self.name).stem

    @property
    def parts(self):
        return tuple(self._path.split("/")) if _remote(self) else LocalPath(self).parts

    @property
    def parent(self):
        if _remote(self):
            scheme, rest = self._path.split("://", 1)
            return Path(scheme + "://" + rest.rsplit("/", 1)[0]) if "/" in rest else self
        return Path(LocalPath(self).parent)

    @property
    def parents(self):
        result, p = [], self
        while p.parent != p:
            p = p.parent
            result.append(p)
        return tuple(result)

    def joinpath(self, *parts):
        return Path(self, *parts)

    def with_suffix(self, suffix):
        return self.parent / PurePosixPath(self.name).with_suffix(suffix)

    def with_name(self, name):
        return self.parent / name

    def relative_to(self, other):
        raw = str(Path(other)).rstrip("/")
        if self._path == raw:
            return Path(".")
        if not self._path.startswith(raw + "/"):
            raise ValueError(f"{self} is not below {other}")
        return Path(self._path[len(raw) + 1:])

    def is_absolute(self):
        return _remote(self) or LocalPath(self).is_absolute()

    def expanduser(self):
        return self if _remote(self) else Path(LocalPath(self).expanduser())

    def resolve(self, strict=False):
        return self if _remote(self) else Path(LocalPath(self).resolve(strict=strict))

    @classmethod
    def cwd(cls):
        return cls(LocalPath.cwd())

    def exists(self):
        if self._directory is not None:
            return True
        return current_io().exists(self)

    def is_dir(self):
        return self._directory if self._directory is not None else current_io().is_dir(self)

    def is_file(self):
        if self._directory is not None:
            return not self._directory
        client = current_io()
        if client._write_sink is not None and client._write_sink.matches(self):
            return client.exists(self) and not client.is_dir(self)
        if not _remote(self):
            return LocalPath(self).is_file()
        try:
            current_io().stat(self)
            return True
        except FileNotFoundError:
            return False

    def stat(self):
        return current_io().stat(self)

    def mkdir(self, mode=0o777, parents=False, exist_ok=False):
        if not _remote(self):
            LocalPath(self).mkdir(mode=mode, parents=parents, exist_ok=exist_ok)

    def iterdir(self):
        return current_io().iterdir(self)

    def glob(self, pattern):
        return current_io().glob(self, pattern)

    def rglob(self, pattern):
        return self.glob("**/" + pattern)

    def open(self, mode="r", **kwargs):
        return open_file(self, mode, **kwargs)

    def read_bytes(self):
        return read_bytes(self)

    def read_text(self, encoding="utf-8", errors=None):
        return read_bytes(self).decode(encoding, errors or "strict")

    def write_bytes(self, data, *, asynchronous=False):
        return write_bytes(self, data, asynchronous=asynchronous)

    def write_text(self, data, encoding="utf-8", errors=None, *, asynchronous=False):
        return write_bytes(self, data.encode(encoding, errors or "strict"), asynchronous=asynchronous)

    def unlink(self, missing_ok=False):
        current_io().remove(self,missing_ok=missing_ok)


def copy_tree(source, destination, *, exclude=()):
    """Copy files via the unified API, merging existing checkpoint directories."""
    source, destination = Path(source), Path(destination)
    deferred = []
    for path in source.rglob("*"):
        if path.is_file() and path.name not in exclude:
            if path.name == "_view_done":
                deferred.append(path)
                continue
            write_bytes(destination / path.relative_to(source), read_bytes(path))
    for path in deferred:
        write_bytes(destination / path.relative_to(source), read_bytes(path))


def wait_futures(futures):
    """Drain every operation before reporting the first failure."""
    error = None
    for future in futures:
        try:
            future.result()
        except BaseException as exc:
            error = error or exc
    if error is not None:
        raise error


def submit_io(pool, function, *args, **kwargs):
    """Keep the current IO/cache scope when work moves to a background thread."""
    return pool.submit(copy_context().run, function, *args, **kwargs)


def load_trimesh(path, **kwargs):
    """Load meshes and glTF/OBJ external buffers/textures through unified IO."""
    import trimesh
    from trimesh.resolvers import Resolver
    path = Path(path)

    class AssetResolver(Resolver):
        def __init__(self, root):
            self.root = Path(root)

        def get(self, name):
            return read_bytes(self.root / unquote(name))

        def write(self, name, data):
            write_bytes(self.root / name, data)

        def namespaced(self, namespace):
            return AssetResolver(self.root / namespace)

        def keys(self):
            return (str(p.relative_to(self.root)) for p in self.root.rglob("*") if p.is_file())

    return trimesh.load(io.BytesIO(read_bytes(path)), file_type=path.suffix.lstrip("."),
                        resolver=AssetResolver(path.parent), **kwargs)


def load_image(path):
    from PIL import Image
    image = Image.open(io.BytesIO(read_bytes(path)))
    image.load()
    return image


def save_image(image, path, *, asynchronous=False, **kwargs):
    buffer = io.BytesIO()
    fmt = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG"}[Path(path).suffix.lower()]
    image.save(buffer, format=fmt, **kwargs)
    return write_bytes(path, buffer.getvalue(), asynchronous=asynchronous)


def load_bitmap(path):
    import mitsuba as mi
    stream = mi.MemoryStream()
    stream.write(read_bytes(path))
    stream.seek(0)
    return mi.Bitmap(stream)


def write_bitmap(bitmap, path, *, asynchronous=False):
    import mitsuba as mi
    fmt = {".png": mi.Bitmap.FileFormat.PNG, ".exr": mi.Bitmap.FileFormat.OpenEXR,
           ".jpg": mi.Bitmap.FileFormat.JPEG, ".jpeg": mi.Bitmap.FileFormat.JPEG}[Path(path).suffix.lower()]
    stream = mi.MemoryStream()
    bitmap.write(stream, fmt)
    stream.seek(0)
    return write_bytes(path, stream.read(stream.size()), asynchronous=asynchronous)


@contextmanager
def memory_file(data=b"", *, name="asset"):
    """A seekable Linux memory file for native plugins requiring a local filename.

    Keep the context alive until the plugin has loaded the file. The descriptor
    is closed deterministically; no disk staging or cloud bypass is involved.
    """
    if not hasattr(os, "memfd_create") or not LocalPath("/proc/self/fd").is_dir():
        raise RuntimeError("memory_file requires Linux memfd and /proc/self/fd; use local_file otherwise")
    fd = os.memfd_create(name, flags=os.MFD_CLOEXEC)
    try:
        with os.fdopen(os.dup(fd), "wb") as stream:
            stream.write(data)
        os.lseek(fd, 0, os.SEEK_SET)
        yield f"/proc/self/fd/{fd}"
    finally:
        os.close(fd)


@contextmanager
def local_file(data, *, name="asset"):
    """A scoped fallback for packages requiring a regular file on local disk."""
    with tempfile.TemporaryDirectory(prefix="oss-io-native-") as root:
        path = LocalPath(root) / LocalPath(name).name
        path.write_bytes(data)
        yield str(path)


_native_stack = ContextVar("oss_io_native_files", default=None)


@contextmanager
def native_file_scope():
    """Own native memory files and local fallbacks until a with block exits."""
    with ExitStack() as stack:
        token = _native_stack.set(stack)
        try:
            yield
        finally:
            _native_stack.reset(token)


def native_files(function):
    """Keep native-plugin files alive for one function invocation."""
    @wraps(function)
    def wrapped(*args, **kwargs):
        with native_file_scope():
            return function(*args, **kwargs)
    return wrapped


def native_path(path, *, data=None, memory=True):
    stack = _native_stack.get()
    if stack is None:
        if data is not None or _remote(path):
            raise RuntimeError("native staging requires native_file_scope or @native_files")
        return str(path)
    factory = memory_file if memory else local_file
    return stack.enter_context(factory(
        read_bytes(path) if data is None else data, name=Path(path).name))


@contextmanager
def open_file(path, mode="rb", *, encoding="utf-8", **kwargs):
    """Local streaming or seekable remote memory IO, including native consumers.

    Remote writes publish only on successful context exit. Local streams avoid
    copying cache files into additional Python buffers. Append is local-only.
    """
    path = os.fspath(path)
    if not _remote(path):
        if any(flag in mode for flag in "wax+"):
            LocalPath(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, mode, **({} if "b" in mode else {"encoding": encoding}), **kwargs) as stream:
            yield stream
        return
    if mode not in {"rb", "r", "wb", "w"}:
        raise ValueError("remote open supports only r/rb/w/wb")
    writing = mode.startswith("w")
    raw = io.BytesIO(b"" if writing else read_bytes(path))
    stream = raw if "b" in mode else io.TextIOWrapper(raw, encoding=encoding, **kwargs)
    try:
        yield stream
        if writing:
            stream.flush()
            write_bytes(path, raw.getvalue())
    finally:
        stream.close()


def copy_file(source, destination):
    """Copy through the common backend; publish local destinations atomically."""
    import shutil
    destination = os.fspath(destination)
    client = current_io()
    if _remote(destination) and (client._record_writes or
            (client._write_sink is not None and client._write_sink.matches(destination))):
        write_bytes(destination, read_bytes(source))
        return
    if destination.startswith("oss://"):
        import alibabacloud_oss_v2 as oss
        client = current_io()
        bucket, key = _oss_parts(destination)
        source = os.fspath(source)

        def perform():
            with _copy_source(source) as (stream, size):
                _oss_upload(client._oss(), oss.PutObjectRequest(bucket=bucket, key=key), stream, size,
                            filepath=source if not _remote(source) else None)
                with client._lock:
                    client.bytes_written += size

        client._ordered_write(destination, perform)
        return
    if _remote(destination):
        write_bytes(destination, read_bytes(source))
        return
    target = LocalPath(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=target.name + ".tmp.", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as output, _copy_source(source) as (input, size):
            shutil.copyfileobj(input, output, 4 << 20)
        os.replace(temporary, target)
    finally:
        LocalPath(temporary).unlink(missing_ok=True)


@contextmanager
def _copy_source(path):
    """Stream large copies, retaining cache/budget/sink semantics when requested."""
    path = os.fspath(path)
    client = current_io()
    if _remote(path) and (client._write_sink is not None or client._cache_reads or client._read_budget is not None):
        data = read_bytes(path)
        with io.BytesIO(data) as stream:
            yield stream, len(data)
    elif path.startswith("oss://"):
        import alibabacloud_oss_v2 as oss
        bucket, key = _oss_parts(path)
        try:
            result = client._oss().get_object(oss.GetObjectRequest(bucket=bucket, key=key))
        except (oss.exceptions.ServiceError, oss.exceptions.OperationError) as exc:
            if _missing_oss_key(exc):
                raise FileNotFoundError(path) from exc
            raise
        try:
            yield result.body, result.content_length
            with client._lock:
                client.bytes_read += result.content_length
                client.remote_bytes_read += result.content_length
        finally:
            result.body.close()
    elif path.startswith("s3://"):
        import megfile
        with megfile.smart_open(path, "rb") as stream:
            size = client.stat(path).st_size
            yield stream, size
            with client._lock:
                client.bytes_read += size
                client.remote_bytes_read += size
    else:
        with open(path, "rb") as stream:
            size = os.fstat(stream.fileno()).st_size
            yield stream, size
            with client._lock:
                client.bytes_read += size


def torch_load(path, *args, **kwargs):
    import torch
    if hasattr(path, "read"):
        return torch.load(path, *args, **kwargs)
    # Native memory files also support torch's mmap reader without disk staging.
    if kwargs.get("mmap") and _remote(path):
        with memory_file(read_bytes(path), name=Path(path).name) as local:
            return torch.load(local, *args, **kwargs)
    if kwargs.get("mmap"):
        return torch.load(path, *args, **kwargs)
    with open_file(path, "rb") as stream:
        return torch.load(stream, *args, **kwargs)


def torch_save(value, path, *, asynchronous=False, **kwargs):
    import torch
    if hasattr(path, "write"):
        return torch.save(value, path, **kwargs)
    with io.BytesIO() as stream:
        torch.save(value, stream, **kwargs)
        return write_bytes(path, stream.getvalue(), asynchronous=asynchronous)


@contextmanager
def memory_directory():
    """A real, writable tmpfs directory for native readers, writers and subprocesses."""
    try:
        mounts = LocalPath("/proc/self/mountinfo").read_text().splitlines()
        tmpfs = any(line.split()[4] == "/dev/shm" and
                    line.split(" - ", 1)[1].split()[0] == "tmpfs" for line in mounts)
    except OSError:
        tmpfs = False
    if not tmpfs or not LocalPath("/dev/shm").is_dir():
        raise RuntimeError("memory_directory requires a Linux /dev/shm tmpfs mount")
    with tempfile.TemporaryDirectory(prefix="oss-io-native-", dir="/dev/shm") as local:
        yield local


@contextmanager
def native_directory(root, files):
    """Expose an explicit object inventory to libraries that open files internally.

    Directory entries live on tmpfs; file data lives in scoped memfds. Never
    enumerate remote prefixes, and reject paths escaping the supplied inventory.
    """
    with memory_directory() as local, ExitStack() as stack:
        for relative in files:
            rel = PurePosixPath(relative)
            if rel.is_absolute() or ".." in rel.parts:
                raise ValueError("invalid native inventory path")
            target = LocalPath(local) / str(rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            fd_path = stack.enter_context(memory_file(read_bytes(Path(root) / relative), name=rel.name))
            target.symlink_to(fd_path)
        yield local


def safetensors_load(path):
    from safetensors.torch import load_file
    with memory_file(read_bytes(path), name=Path(path).name) as local:
        return load_file(local)


def read_pinned(path, etag=None):
    """Conditional object read for immutable inventory consumers."""
    path = os.fspath(path)
    if path.startswith("oss://") and etag:
        import alibabacloud_oss_v2 as oss
        bucket, key = _oss_parts(path)
        response = current_io()._oss().get_object(oss.GetObjectRequest(
            bucket=bucket, key=key, if_match=etag))
        try:
            return response.body.read()
        finally:
            response.body.close()
    data = read_bytes(path)
    if etag and hashlib.sha256(data).hexdigest() != etag:
        raise ValueError("local input changed since inventory: " + path)
    return data


def write_object(path, data, *, overwrite=True, metadata=None):
    """Synchronous publication with optional create-only semantics and metadata."""
    path, data = os.fspath(path), bytes(data)
    if path.startswith("oss://"):
        import alibabacloud_oss_v2 as oss
        bucket, key = _oss_parts(path)
        try:
            client = current_io()
            request = oss.PutObjectRequest(bucket=bucket, key=key,
                                          metadata=metadata, forbid_overwrite=not overwrite)
            def perform():
                _oss_upload(client._oss(), request, data, len(data))
                with client._lock:
                    client.bytes_written += len(data)
                    if client._record_writes:
                        client.written_objects[path] = {
                            "size": len(data), "md5": hashlib.md5(data).hexdigest()}
            client._ordered_write(path, perform)
        except (oss.exceptions.ServiceError, oss.exceptions.OperationError) as exc:
            error = exc.unwrap() if hasattr(exc, "unwrap") else exc
            if getattr(error, "code", None) in {"FileAlreadyExists", "ObjectAlreadyExists"}:
                raise FileExistsError(path) from exc
            raise
    elif _remote(path):
        if not overwrite or metadata:
            raise NotImplementedError("conditional writes/metadata require OSS or local files")
        write_bytes(path, data)
    else:
        target = LocalPath(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if overwrite:
            write_bytes(path, data)
        else:
            fd, temporary = tempfile.mkstemp(dir=target.parent)
            try:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(data)
                os.link(temporary, target)
            finally:
                LocalPath(temporary).unlink(missing_ok=True)


@contextmanager
def native_package(root):
    """Stage a remote package using its explicit _io_files.json inventory."""
    if not _remote(root):
        yield os.fspath(root)
        return
    import json
    files = json.loads(read_text(Path(root) / '_io_files.json'))
    if not isinstance(files, list) or not all(isinstance(p, str) for p in files):
        raise ValueError('package inventory must be a list of relative filenames')
    with native_directory(root, files) as local:
        yield local


def publish_package(source, destination):
    """Publish package files, then their explicit native-loader inventory."""
    import json
    source = Path(source)
    files = [str(path.relative_to(source)) for path in source.rglob('*')
             if path.is_file() and path.name != '_io_files.json']
    for relative in files:
        copy_file(source / relative, Path(destination) / relative)
    write_text(Path(destination) / '_io_files.json', json.dumps(files))


def imageio_read(path, *args, **kwargs):
    import imageio.v2 as imageio
    source = io.BytesIO(read_bytes(path)) if isinstance(path, (str, os.PathLike)) else path
    return imageio.imread(source, *args, **kwargs)


def imageio_write(path, array, **kwargs):
    import imageio.v2 as imageio
    kwargs.setdefault('format', Path(path).suffix)
    write_bytes(path, imageio.imwrite('<bytes>', array, **kwargs))


def cv2_read(path, flags=None):
    import cv2
    import numpy as np
    return cv2.imdecode(np.frombuffer(read_bytes(path), dtype=np.uint8),
                        cv2.IMREAD_COLOR if flags is None else flags)


def cv2_write(path, array, params=None):
    import cv2
    ok, data = cv2.imencode(Path(path).suffix, array, params or [])
    if ok:
        write_bytes(path, data.tobytes())
    return ok


def numpy_load(path, *args, **kwargs):
    import numpy as np
    if not isinstance(path, (str, os.PathLike)):
        return np.load(path, *args, **kwargs)
    if kwargs.get('mmap_mode'):
        with memory_file(read_bytes(path), name=Path(path).name) as local:
            return np.load(local, *args, **kwargs)
    return np.load(io.BytesIO(read_bytes(path)), *args, **kwargs)


def read_array(stream, dtype, count=-1):
    """np.fromfile equivalent for seekable local/remote streams."""
    import numpy as np
    size = -1 if count < 0 else count * np.dtype(dtype).itemsize
    return np.frombuffer(stream.read(size), dtype=dtype).copy()



def _after_fork():
    # DataLoader workers must not inherit parent upload futures or locks held
    # by publishing threads. Recreate owned SDK connections lazily in the child.
    for client in list(_instances):
        client._lock = threading.RLock()
        client._pool = None
        client._pending = []
        client._tails = {}
        if client._owns_oss_client:
            client._oss_client = None
        client.bytes_read = client.bytes_written = client.remote_bytes_read = client.cache_hits = 0


if hasattr(os, 'register_at_fork'):
    os.register_at_fork(after_in_child=_after_fork)
