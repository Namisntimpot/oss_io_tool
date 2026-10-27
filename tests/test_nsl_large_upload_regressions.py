"""Ported from NSLv2 infra/tests/test_oss_multipart.py; no real cloud access."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import threading
from types import SimpleNamespace

import pytest

import oss_io_tool as S
import importlib
upload_module = importlib.import_module("oss_io_tool._oss_upload")
from oss_io_tool.publisher import ArtifactPublisher

oss = pytest.importorskip('alibabacloud_oss_v2')
PART = 64 << 10


def result(**kwargs):
    return SimpleNamespace(etag='etag', version_id=None, hash_crc64=None,
                           status='', status_code=200, request_id='test',
                           headers={}, **kwargs)


def already_exists(operation):
    return oss.exceptions.OperationError(name=operation, error=
        oss.exceptions.ServiceError(code='FileAlreadyExists', status_code=409,
            request_id='test', message='already exists', ec='test',
            timestamp='test', request_target='test'))


class MultipartAPI:
    def __init__(self):
        self.objects = {}
        self.uploads = {}
        self.calls = []
        self.lock = threading.Lock()
        self.fail_part = False
        self.fail_complete = False
        self.fail_abort = False
        self.count_only = False

    def put_object(self, request):
        assert isinstance(request.body, bytes), 'small PUT must be bounded bytes'
        assert len(request.body) < upload_module._SINGLE_PUT_LIMIT, 'oversized simple PUT'
        self.calls.append(('put', request))
        key = (request.bucket, request.key)
        if request.forbid_overwrite and key in self.objects:
            raise already_exists('PutObject')
        self.objects[key] = request.body
        return result()

    def initiate_multipart_upload(self, request):
        with self.lock:
            upload_id = str(len(self.calls))
            self.calls.append(('init', request))
            self.uploads[upload_id] = {'parts': {}, 'metadata': request.metadata}
        return result(upload_id=upload_id)

    def upload_part(self, request):
        if self.fail_part:
            raise OSError('injected part failure')
        if self.count_only:
            # Verify a >5 GiB sparse file without allocating/reading 5 GiB.
            size = len(request.body)
            assert size <= upload_module._MIN_PART_SIZE
            assert len(request.body.read(1)) == 1
            data = size
        else:
            chunks = []
            while chunk := request.body.read(8192):
                chunks.append(chunk)
            data = b''.join(chunks)
        with self.lock:
            self.calls.append(('part', request.part_number))
            self.uploads[request.upload_id]['parts'][request.part_number] = data
        return result()

    def complete_multipart_upload(self, request):
        self.calls.append(('complete', request))
        if self.fail_complete:
            raise OSError('injected completion failure')
        key = (request.bucket, request.key)
        if request.forbid_overwrite and key in self.objects:
            raise already_exists('CompleteMultipartUpload')
        parts = self.uploads[request.upload_id]['parts']
        ordered = [parts[p.part_number] for p in request.complete_multipart_upload.parts]
        self.objects[key] = sum(ordered) if self.count_only else b''.join(ordered)
        del self.uploads[request.upload_id]
        return result()

    def abort_multipart_upload(self, request):
        self.calls.append(('abort', request))
        if self.fail_abort:
            raise OSError('injected abort failure')
        del self.uploads[request.upload_id]
        return result()

    def list_parts(self, request):
        pytest.fail('upload must not discover/list cloud state')


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(upload_module, '_SINGLE_PUT_LIMIT', PART)
    monkeypatch.setattr(upload_module, '_MIN_PART_SIZE', PART)
    monkeypatch.setattr(upload_module, '_PART_ALIGNMENT', 1)
    return MultipartAPI()


@pytest.mark.parametrize('size', [0, PART-1, PART, PART*2+19])
def test_bytes_select_safe_upload_and_publish_complete_object(api, size, monkeypatch):
    payload = b'z' * size
    def no_disk(*args, **kwargs):
        pytest.fail('OSS upload must not spool to local disk')
    monkeypatch.setattr(S.tempfile, 'mkstemp', no_disk)
    monkeypatch.setattr(S.tempfile, 'TemporaryDirectory', no_disk)
    with S.IO(oss_client=api, record_writes=True) as client:
        client.write_bytes('oss://bucket/latest.ckpt', payload)
        assert client.bytes_written == size
        assert client.written_objects['oss://bucket/latest.ckpt']['md5'] == hashlib.md5(payload).hexdigest()
    assert api.objects[('bucket', 'latest.ckpt')] == payload
    assert [name for name, _ in api.calls].count('init') == int(size >= PART)
    assert not api.uploads


@pytest.mark.parametrize('size', [0, 3, PART - 1])
def test_small_local_copy_uses_bounded_simple_put(api, tmp_path, size):
    source = tmp_path / 'small-stage'
    payload = b'x' * size
    source.write_bytes(payload)
    with S.io_session(oss_client=api, record_writes=True) as client:
        S.copy_file(source, 'oss://bucket/small.ckpt')
        assert client.bytes_read == client.bytes_written == size
        assert client.written_objects['oss://bucket/small.ckpt']['md5'] == hashlib.md5(payload).hexdigest()
    assert api.objects[('bucket', 'small.ckpt')] == payload
    assert [name for name, _ in api.calls] == ['put']
    assert source.is_file()


def test_large_local_copy_streams_and_preserves_accounting(api, tmp_path, monkeypatch):
    source = tmp_path/'stage'
    payload = bytes(range(256)) * 1025
    source.write_bytes(payload)
    def no_whole_file(*args, **kwargs):
        pytest.fail('checkpoint copy allocated whole-file bytes')
    monkeypatch.setattr(S.LocalPath, 'read_bytes', no_whole_file)
    with S.io_session(oss_client=api, record_writes=True) as client:
        S.copy_file(source, 'oss://bucket/latest.ckpt')
        assert client.bytes_read == client.bytes_written == len(payload)
        assert client.written_objects['oss://bucket/latest.ckpt']['md5'] == hashlib.md5(payload).hexdigest()
    assert api.objects[('bucket', 'latest.ckpt')] == payload
    assert source.is_file()


def test_file_above_simple_put_limit_never_materializes_or_lists(api, tmp_path, monkeypatch):
    monkeypatch.setattr(upload_module, '_MIN_PART_SIZE', 64 << 20)
    api.count_only = True
    size = 5 * (1 << 30) + 17
    source = tmp_path/'sparse-stage'
    with source.open('wb') as stream:
        stream.truncate(size)
    monkeypatch.setattr(S.LocalPath, 'read_bytes', lambda *a: pytest.fail('whole-file read'))
    with S.io_session(oss_client=api) as client:
        S.copy_file(source, 'oss://bucket/large.ckpt')
        assert client.bytes_written == size
    assert api.objects[('bucket', 'large.ckpt')] == size
    assert all(name != 'put' for name, _ in api.calls)


@pytest.mark.parametrize('multipart', [False, True])
def test_metadata_and_create_only_are_preserved_at_completion(api, multipart):
    payload = b'x' * (PART*2 if multipart else 3)
    with S.io_session(oss_client=api):
        S.write_object('oss://bucket/state', payload, overwrite=False, metadata={'step':'55000'})
        assert api.objects[('bucket', 'state')] == payload
        requests = [r for name, r in api.calls if name in {'put','init','complete'}]
        assert all(r.forbid_overwrite for r in requests)
        assert requests[0].metadata == {'step':'55000'}
        with pytest.raises(FileExistsError):
            S.write_object('oss://bucket/state', b'y'*len(payload), overwrite=False)
    assert api.objects[('bucket', 'state')] == payload
    assert not api.uploads


@pytest.mark.parametrize('failure', ['part','complete','abort'])
def test_failed_upload_keeps_old_object_stage_and_reports_immediately(api, tmp_path, caplog, failure):
    source = tmp_path/'stage'
    source.write_bytes(b'n'*(PART*2+3))
    api.objects[('bucket','latest.ckpt')] = b'old valid checkpoint'
    api.fail_part = failure in {'part','abort'}
    api.fail_complete = failure == 'complete'
    api.fail_abort = failure == 'abort'
    callbacks = []
    with S.io_session(oss_client=api) as client:
        publisher = ArtifactPublisher()
        publisher.submit(source, 'oss://bucket/latest.ckpt', on_published=lambda:callbacks.append(True))
        assert publisher._pending[0].exception(timeout=5) is not None
        assert 'Artifact publication failed' in caplog.text
        with pytest.raises(Exception, match='injected (part|completion) failure'):
            publisher.drain()
        assert client.bytes_written == 0
    assert source.is_file() and not callbacks
    assert api.objects[('bucket','latest.ckpt')] == b'old valid checkpoint'
    assert any(name == 'abort' for name, _ in api.calls)
    if failure != 'abort':
        assert not api.uploads


def test_copy_waits_for_prior_async_write_to_same_object(api, tmp_path, monkeypatch):
    source = tmp_path/'stage'
    payload = b'new checkpoint'*(PART//4)
    source.write_bytes(payload)
    entered, release = threading.Event(), threading.Event()
    original = api.put_object
    def delayed(request):
        entered.set()
        assert release.wait(5)
        return original(request)
    monkeypatch.setattr(api, 'put_object', delayed)
    with S.io_session(oss_client=api) as client, ThreadPoolExecutor(1) as pool:
        first = client.write_bytes('oss://bucket/latest.ckpt', b'old', asynchronous=True)
        assert entered.wait(2)
        second = S.submit_io(pool, S.copy_file, source, 'oss://bucket/latest.ckpt')
        try:
            assert not second.done()
        finally:
            release.set()
        first.result(timeout=5)
        second.result(timeout=5)
    assert api.objects[('bucket','latest.ckpt')] == payload


def test_async_multipart_snapshots_input_and_flush_propagates_failure(api):
    payload = bytearray(b'x'*(PART*2+1))
    with S.IO(oss_client=api) as client:
        client.write_bytes('oss://bucket/good', payload, asynchronous=True)
        payload[:] = b'y'*len(payload)
        client.flush()
    assert api.objects[('bucket','good')] == b'x'*len(payload)
    client = S.IO(oss_client=api)
    api.fail_complete = True
    client.write_bytes('oss://bucket/bad', b'z'*len(payload), asynchronous=True)
    with pytest.raises(Exception, match='injected completion failure'):
        client.flush()
    assert ('bucket','bad') not in api.objects
    client.close()
