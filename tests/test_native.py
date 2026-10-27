import json
from pathlib import Path
import pytest
from oss_io_tool import copy_file, open_file, memory_file


def test_atomic_copy_failure_preserves_published_file(tmp_path):
    destination = tmp_path / 'published'
    destination.write_bytes(b'previous')
    with pytest.raises(FileNotFoundError):
        copy_file(tmp_path / 'missing', destination)
    assert destination.read_bytes() == b'previous'
    assert list(tmp_path.glob('*.tmp.*')) == []


def test_native_memory_file_lifetime():
    with memory_file(b'asset') as path:
        assert Path(path).read_bytes() == b'asset'
    assert not Path(path).exists()


def test_common_stream_roundtrip(tmp_path):
    path = tmp_path / 'file'
    with open_file(path, 'w') as output:
        output.write('文字')
    with open_file(path, 'r') as source:
        assert source.read() == '文字'


def test_native_directory_scopes_internal_library_reads(tmp_path):
    from oss_io_tool import native_directory
    source = tmp_path / 'weights'
    (source / 'configs').mkdir(parents=True)
    (source / 'configs/model.json').write_bytes(b'configuration')
    (source / 'model.pt').write_bytes(b'weights')
    with native_directory(source, ['configs/model.json', 'model.pt']) as root:
        local = Path(root)
        assert (local / 'configs/model.json').read_bytes() == b'configuration'
        assert (local / 'model.pt').read_bytes() == b'weights'
    assert not local.exists()
    with pytest.raises(ValueError, match='invalid'):
        with native_directory(source, ['../escape']):
            pass


def test_failed_upload_preserves_stage_and_surfaces_error(tmp_path):
    from oss_io_tool.publisher import ArtifactPublisher
    source = tmp_path / 'stage'
    source.write_bytes(b'checkpoint')
    destination = tmp_path / 'parent-is-file' / 'checkpoint'
    destination.parent.write_bytes(b'not a directory')
    published = []
    queue = ArtifactPublisher()
    queue.submit(source, destination, on_published=lambda: published.append(True))
    with pytest.raises(OSError):
        queue.drain()
    assert source.read_bytes() == b'checkpoint'
    assert not published
    assert queue._closed


def test_background_publisher_preserves_io_session(tmp_path):
    from oss_io_tool import io_session
    from oss_io_tool.publisher import ArtifactPublisher
    class Sink:
        def __init__(self):
            self.files = {}
        def matches(self, path):
            return str(path).startswith('oss://')
        def write(self, path, data):
            self.files[str(path)] = bytes(data)
    source = tmp_path / 'checkpoint'
    source.write_bytes(b'snapshot')
    sink = Sink()
    with io_session(write_sink=sink):
        publisher = ArtifactPublisher()
        publisher.submit(source, 'oss://test/checkpoint')
        publisher.drain()
    assert sink.files == {'oss://test/checkpoint': b'snapshot'}


def test_indexed_directory_lookups_never_list_remote(monkeypatch):
    import oss_io_tool
    from oss_io_tool.index import IndexedFilesystem
    monkeypatch.setattr(oss_io_tool, 'read_bytes', lambda path: json.dumps([
        'scene/001_left.jpg', 'scene/001_right.jpg', 'scene/002_left.jpg',
        'other/001_left.jpg', '.hidden/config.json']).encode())
    fs = IndexedFilesystem('oss://bucket/data')
    assert fs.smart_listdir('oss://bucket/data/scene') == [
        '001_left.jpg', '001_right.jpg', '002_left.jpg']
    assert fs.smart_glob('oss://bucket/data/scene/001*') == [
        'oss://bucket/data/scene/001_left.jpg', 'oss://bucket/data/scene/001_right.jpg']
    assert fs.smart_listdir('oss://bucket/data/.hidden') == ['config.json']


def test_forked_workers_do_not_inherit_upload_locks_or_futures(tmp_path):
    import os
    import select
    import threading
    from concurrent.futures import Future
    from oss_io_tool import IO
    if not hasattr(os, 'fork'):
        pytest.skip('POSIX fork required')
    path = tmp_path / 'sample'
    path.write_bytes(b'bytes')
    storage = IO()
    future = Future()
    storage._pending.append(future)
    held, release = threading.Event(), threading.Event()
    def hold():
        with storage._lock:
            held.set()
            release.wait(5)
    thread = threading.Thread(target=hold)
    thread.start()
    assert held.wait(2)
    read_fd, write_fd = os.pipe()
    child = os.fork()
    if child == 0:
        os.close(read_fd)
        storage.flush()
        os.write(write_fd, storage.read_bytes(path))
        os._exit(0)
    os.close(write_fd)
    try:
        ready, _, _ = select.select([read_fd], [], [], 3)
        assert ready, 'child inherited a parent upload lock or unresolved future'
        assert os.read(read_fd, 5) == b'bytes'
    finally:
        release.set()
        thread.join()
        future.set_result(None)
        os.kill(child, 9) if not ready else None
        os.waitpid(child, 0)
        os.close(read_fd)
        storage.close()
