"""Extracted IO contracts: real SDK request models with an in-memory service."""
import io
import sys
import threading
from types import SimpleNamespace
import numpy as np
import pytest
import oss_io_tool as S


def service_error(code):
    import alibabacloud_oss_v2 as oss
    return oss.exceptions.OperationError(name="test", error=oss.exceptions.ServiceError(
        code=code, status_code=404 if code == "NoSuchKey" else 403, request_id="test",
        message=code, ec="test", timestamp="test", request_target="test"))


class FakeOSS:
    def __init__(self):
        self.objects = {}
        self.writes = []

    def put_object(self, request):
        self.objects[(request.bucket, request.key)] = request.body
        self.writes.append(request.key)

    def get_object(self, request):
        import alibabacloud_oss_v2 as oss
        try:
            data = self.objects[(request.bucket, request.key)]
        except KeyError:
            raise service_error("NoSuchKey")
        if request.range_header:
            first, last = (int(x) for x in request.range_header.removeprefix("bytes=").split("-"))
            data = data[first:last+1]
        return SimpleNamespace(body=io.BytesIO(data), content_length=len(data))

    def head_object(self, request):
        import alibabacloud_oss_v2 as oss
        import hashlib
        from datetime import datetime, timezone
        try:
            data = self.objects[(request.bucket, request.key)]
        except KeyError:
            raise service_error("NoSuchKey")
        return SimpleNamespace(content_length=len(data), etag=hashlib.md5(data).hexdigest(),
                               last_modified=datetime(2026, 1, 1, tzinfo=timezone.utc))

    def list_objects_v2(self, request):
        contents, prefixes = [], set()
        for bucket, key in sorted(self.objects):
            if bucket != request.bucket or not key.startswith(request.prefix):
                continue
            rest = key[len(request.prefix):]
            if request.delimiter and "/" in rest:
                prefixes.add(request.prefix + rest.split("/", 1)[0] + "/")
            else:
                contents.append(SimpleNamespace(key=key))
        return SimpleNamespace(contents=contents,
                               common_prefixes=[SimpleNamespace(prefix=p) for p in sorted(prefixes)],
                               is_truncated=False)


@pytest.fixture
def cloud(monkeypatch):
    fake = FakeOSS()
    client = S.IO(oss_client=fake)
    monkeypatch.setattr(S, "default_io", client)
    monkeypatch.setattr(S, "read_bytes", client.read_bytes)
    monkeypatch.setattr(S, "write_bytes", client.write_bytes)
    yield client, fake
    client.close()


@pytest.mark.parametrize("prefix", ["local", "oss", "s3"])
def test_sync_and_async_dispatch(tmp_path, monkeypatch, prefix):
    fake = FakeOSS()
    calls = []
    data = {}

    class Stream(io.BytesIO):
        def __init__(self, path, mode):
            super().__init__(data.get(path, b""))
            self.path, self.mode = path, mode

        def close(self):
            if "w" in self.mode:
                data[self.path] = self.getvalue()
            super().close()

    def smart_open(path, mode):
        calls.append((path, mode))
        return Stream(path, mode)

    monkeypatch.setitem(sys.modules, "megfile", SimpleNamespace(smart_open=smart_open))
    path = str(tmp_path / "sub/file") if prefix == "local" else prefix + "://bucket/sub/file"
    with S.IO(oss_client=fake) as client:
        assert client.write_text(path, "同步") is None
        assert client.read_text(path) == "同步"
        payload = bytearray(b"snapshot")
        future = client.write_bytes(path, payload, asynchronous=True)
        payload[:] = b"mutated!"
        future.result(timeout=5)
        client.flush()
        assert client.read_bytes(path) == b"snapshot"
        assert client.bytes_read > 0 and client.bytes_written > 0
    if prefix == "s3":
        assert all(p.startswith("s3://") for p, mode in calls)
        assert {mode for p, mode in calls} == {"rb", "wb"}


def test_async_returns_before_upload_and_sync_waits(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    client = S.IO(workers=2)
    original = client._put

    def slow(path, data):
        entered.set()
        assert release.wait(5)
        original(path, data)

    monkeypatch.setattr(client, "_put", slow)
    path = tmp_path / "data"
    future = client.write_bytes(path, b"first", asynchronous=True)
    assert entered.wait(5) and not future.done() and not path.exists()
    done = threading.Event()
    t = threading.Thread(target=lambda: (client.write_bytes(path, b"last"), done.set()))
    t.start()
    assert not done.wait(0.05)
    release.set()
    t.join(5)
    assert done.is_set()
    client.close()
    assert path.read_bytes() == b"last"


def test_flush_waits_for_all_writes_and_propagates_failure(tmp_path, monkeypatch):
    client = S.IO()
    original = client._put

    def fail(path, data):
        if data == b"fail":
            raise OSError("upload failed")
        original(path, data)

    monkeypatch.setattr(client, "_put", fail)
    client.write_bytes(tmp_path / "bad", b"fail", asynchronous=True)
    good = client.write_bytes(tmp_path / "good", b"ok", asynchronous=True)
    with pytest.raises(OSError, match="upload failed"):
        client.flush()
    assert good.done() and (tmp_path / "good").read_bytes() == b"ok"
    client.close()


def test_cloud_paths_listing_and_missing(cloud):
    client, fake = cloud
    root = S.Path("oss://bucket/assets")
    assert str(root / "texture.png") == "oss://bucket/assets/texture.png"
    assert root.parent == S.Path("oss://bucket")
    assert root.parent.parent == root.parent
    root.mkdir(parents=True, exist_ok=True)
    client.write_bytes(root / "one.glb", b"1")
    client.write_bytes(root / "sub/two.glb", b"2")
    for discover in (lambda: list(root.iterdir()), lambda: list(root.rglob("*.glb")), root.is_dir):
        with pytest.raises(S.RemoteListingError):
            discover()
    assert (root / "one.glb").is_file()
    assert not (root / "missing").exists()
    with pytest.raises(FileNotFoundError):
        client.read_bytes(root / "missing")


def test_oss_glob_never_calls_list_api(cloud, monkeypatch):
    client, fake = cloud
    monkeypatch.setattr(fake, "list_objects_v2", lambda request: pytest.fail("OSS listing called"))
    for key in ("one.json", "sub/two.json", "sub/deeper/three.json"):
        client.write_bytes("oss://bucket/root/" + key, b"{}")
    root = S.Path("oss://bucket/root")
    for pattern in ("*/*.json", "**/*.json", "sub", "one.json"):
        with pytest.raises(S.RemoteListingError):
            list(root.glob(pattern))
    assert not (root / "absent.json").exists()


def test_explicit_inventory_requires_opt_in_and_follows_all_pages(cloud, monkeypatch):
    from datetime import datetime, timezone
    client, fake = cloud
    calls = []
    def listing(request):
        calls.append(request.continuation_token)
        page = 0 if request.continuation_token is None else 1
        return SimpleNamespace(contents=[SimpleNamespace(key=f'root/part-{page}.tar', size=page+1,
            etag=str(page), last_modified=datetime(2026, 1, 1, tzinfo=timezone.utc))],
            is_truncated=page == 0, next_continuation_token='next' if page == 0 else None)
    monkeypatch.setattr(fake, 'list_objects_v2', listing)
    with pytest.raises(S.RemoteListingError):
        list(client.inventory('oss://bucket/root'))
    assert calls == []
    rows = list(client.inventory('oss://bucket/root', allow_remote=True))
    assert calls == [None, 'next']
    assert [r['path'] for r in rows] == ['oss://bucket/root/part-0.tar', 'oss://bucket/root/part-1.tar']
    assert [r['size'] for r in rows] == [1, 2]
    monkeypatch.setattr(fake, 'list_objects_v2', lambda request: SimpleNamespace(
        contents=[], is_truncated=True, next_continuation_token=request.continuation_token))
    with pytest.raises(RuntimeError, match='pagination'):
        list(client.inventory('oss://bucket/root', allow_remote=True))


def test_remote_read_cache_uses_etag_and_enforces_download_cap(tmp_path):
    fake = FakeOSS()
    with S.IO(oss_client=fake, cache_reads=True, max_remote_read_bytes=6) as client:
        path = "oss://bucket/object"
        client.write_bytes(path, b"abc")
        assert client.read_bytes(path) == b"abc"
        assert client.read_bytes(path) == b"abc" and client.cache_hits == 1
        # Same size and timestamp, but new ETag: stale content must not be read.
        client.write_bytes(path, b"xyz")
        assert client.read_bytes(path) == b"xyz"
        client.write_bytes(path, b"longer")
        with pytest.raises(RuntimeError, match="budget exceeded"):
            client.read_bytes(path)
        assert client.remote_bytes_read == 6


def test_indexed_range_reads_only_member_and_obeys_budget():
    fake = FakeOSS()
    with S.IO(oss_client=fake, cache_reads=True, max_remote_read_bytes=5) as client:
        client.write_bytes("oss://bucket/nap.tar", b"header"+b"JSON!"+b"unneeded"*100)
        assert client.read_range("oss://bucket/nap.tar", 6, 5) == b"JSON!"
        assert client.read_range("oss://bucket/nap.tar", 6, 5) == b"JSON!"
        assert client.remote_bytes_read == 5 and client.cache_hits == 1
        with pytest.raises(RuntimeError, match="budget exceeded"):
            client.read_range("oss://bucket/nap.tar", 0, 6)


def test_s3_path_metadata_and_listing_use_smart_apis(monkeypatch):
    calls = []

    def record(name, result):
        def call(path, *args):
            calls.append((name, path))
            return result
        return call

    monkeypatch.setitem(sys.modules, "megfile", SimpleNamespace(
        smart_exists=record("exists", True),
        smart_isdir=record("isdir", True),
        smart_listdir=record("listdir", ["child"]),
        smart_glob=record("glob", ["s3://bucket/root/one.png"]),
        smart_stat=record("stat", SimpleNamespace(size=7, mtime=1, extra={"ETag": "version"}))))
    root = S.Path("s3://bucket/root")
    assert root.exists()
    for discover in (root.is_dir, lambda: list(root.iterdir()), lambda: list(root.glob("*.png"))):
        with pytest.raises(S.RemoteListingError):
            discover()
    assert root.stat().st_size == 7 and root.stat().etag == "version"
    assert all(path.startswith("s3://") for name, path in calls)
    assert {name for name, path in calls} == {"stat"}


def test_authorization_errors_are_not_missing(cloud, monkeypatch):
    import alibabacloud_oss_v2 as oss
    client, fake = cloud

    def denied(request):
        raise service_error("AccessDenied")

    monkeypatch.setattr(fake, "head_object", denied)
    with pytest.raises(oss.exceptions.OperationError):
        client.exists("oss://bucket/key")


def test_gltf_external_buffers_and_images_use_io(cloud):
    import trimesh
    from PIL import Image
    client, fake = cloud
    mesh = trimesh.creation.box()
    mesh.visual = trimesh.visual.TextureVisuals(
        uv=np.zeros((len(mesh.vertices), 2)),
        material=trimesh.visual.material.PBRMaterial(baseColorTexture=Image.new("RGB", (2, 2), "red")))
    bundle = trimesh.Scene(mesh).export(file_type="gltf")
    for name, data in bundle.items():
        client.write_bytes("oss://bucket/mesh/" + name, data)
    gltf = next(name for name in bundle if name.endswith(".gltf"))
    loaded = S.load_trimesh("oss://bucket/mesh/" + gltf, force="scene", process=False)
    assert len(loaded.geometry) == 1
    material = next(iter(loaded.geometry.values())).visual.material
    assert material.baseColorTexture.getpixel((0, 0))[:3] == (255, 0, 0)


def test_memory_file_lifetime():
    with S.memory_file(b"native loader", name="mesh.ply") as name:
        assert S.LocalPath(name).read_bytes() == b"native loader"
    assert not S.LocalPath(name).exists()


def test_local_native_fallback_is_removed_on_error():
    with pytest.raises(RuntimeError, match="intentional"):
        with S.native_file_scope():
            path = S.native_path("native.ext", data=b"payload", memory=False)
            assert S.LocalPath(path).read_bytes() == b"payload"
            raise RuntimeError("intentional")
    assert not S.LocalPath(path).exists() and not S.LocalPath(path).parent.exists()


def test_background_io_inherits_session_and_drains_before_cleanup(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    fake = FakeOSS()
    with S.io_session(oss_client=fake, cache_reads=True) as client:
        fake.objects[("bucket", "input")] = b"data"
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert S.submit_io(pool, S.read_bytes, "oss://bucket/input").result() == b"data"
            assert S.submit_io(pool, S.current_io).result() is client
        client.write_bytes("oss://bucket/output", b"data", asynchronous=True)
    assert fake.objects[("bucket", "output")] == b"data" and not client.memory_cache


def test_remote_memory_reuse_never_writes_a_local_cache(monkeypatch):
    fake = FakeOSS()

    def forbidden(*args, **kwargs):
        raise AssertionError("remote IO attempted a local cache write")

    monkeypatch.setattr(S.LocalPath, "write_bytes", forbidden)
    monkeypatch.setattr(S.tempfile, "NamedTemporaryFile", forbidden)
    monkeypatch.setattr(S.tempfile, "TemporaryDirectory", forbidden)
    with S.io_session(oss_client=fake, cache_reads=True) as client:
        S.write_bytes("oss://bucket/object", b"content")
        assert S.read_bytes("oss://bucket/object") == b"content"
        assert S.read_bytes("oss://bucket/object") == b"content"
        assert client.cache_hits == 1
