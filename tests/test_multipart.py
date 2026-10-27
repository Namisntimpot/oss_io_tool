"""Exercise the real SDK multipart manager, using tiny thresholds and no network."""
import io
import hashlib
from types import SimpleNamespace

import pytest
import alibabacloud_oss_v2 as oss
from alibabacloud_oss_v2.uploader import Uploader

import oss_io_tool as S
from oss_io_tool import _oss_upload as upload_function
import importlib
from test_storage import FakeOSS, service_error

upload_module = importlib.import_module('oss_io_tool._oss_upload')


class MultipartOSS(FakeOSS):
    def __init__(self):
        super().__init__()
        self.parts = {}
        self.events = []
        self.fail_part = False
        self.fail_complete = False
        self.complete = None
        self.initiated = None
        self.options = None

    def put_object(self, request):
        self.events.append('put')
        if request.forbid_overwrite and (request.bucket, request.key) in self.objects:
            raise service_error('FileAlreadyExists')
        body = request.body
        self.objects[(request.bucket, request.key)] = body.read() if hasattr(body, 'read') else body
        return oss.PutObjectResult(etag='simple')

    def initiate_multipart_upload(self, request):
        self.events.append('initiate')
        self.initiated = request
        return oss.InitiateMultipartUploadResult(upload_id='test-upload')

    def upload_part(self, request):
        if self.fail_part:
            raise OSError('part failed')
        data = request.body.read() if hasattr(request.body, 'read') else request.body
        self.parts[request.part_number] = data
        self.events.append('part')
        return oss.UploadPartResult(etag=hashlib.md5(data).hexdigest())

    def complete_multipart_upload(self, request):
        self.complete = request
        if self.fail_complete:
            raise OSError('complete failed')
        if request.forbid_overwrite and (request.bucket, request.key) in self.objects:
            raise service_error('FileAlreadyExists')
        self.objects[(request.bucket, request.key)] = b''.join(
            self.parts[p.part_number] for p in request.complete_multipart_upload.parts)
        self.events.append('complete')
        return oss.CompleteMultipartUploadResult(etag='multipart')

    def abort_multipart_upload(self, request):
        self.events.append('abort')
        self.parts.clear()


@pytest.fixture
def multipart(monkeypatch):
    monkeypatch.setattr(upload_module, '_SINGLE_PUT_LIMIT', 16)
    monkeypatch.setattr(upload_module, '_MIN_PART_SIZE', 8)
    monkeypatch.setattr(upload_module, '_PART_ALIGNMENT', 1)
    def uploader(client, **kwargs):
        client.options = kwargs
        return Uploader(client, **kwargs)
    monkeypatch.setattr(oss, 'Uploader', uploader)
    return MultipartOSS()


@pytest.mark.parametrize('size', [0, 16, 17, 100])
@pytest.mark.parametrize('asynchronous', [False, True])
def test_simple_and_multipart_boundary(multipart, size, asynchronous):
    data = bytes(range(size))
    with S.io_session(oss_client=multipart):
        future = S.write_bytes('oss://bucket/data', data, asynchronous=asynchronous)
        if future is not None:
            future.result(timeout=5)
        assert S.read_bytes('oss://bucket/data') == data
    assert ('complete' in multipart.events) == (size >= 16)
    assert ('put' in multipart.events) == (size < 16)


@pytest.mark.parametrize('fail', ['part', 'complete'])
def test_multipart_failure_aborts_preserves_old_object_and_surfaces(multipart, fail):
    multipart.objects[('bucket', 'data')] = b'old'
    setattr(multipart, 'fail_' + fail, True)
    with S.io_session(oss_client=multipart) as storage:
        S.write_bytes('oss://bucket/data', b'x' * 100, asynchronous=True)
        with pytest.raises(OSError, match=fail + ' failed'):
            storage.flush()
    assert multipart.events[-1] == 'abort'
    assert multipart.objects[('bucket', 'data')] == b'old'


def test_multipart_preserves_create_only_and_metadata(multipart):
    with S.io_session(oss_client=multipart):
        S.write_object('oss://bucket/data', b'x' * 100, overwrite=False, metadata={'sha256': 'abc'})
        assert multipart.initiated.metadata == {'sha256': 'abc'}
        assert multipart.complete.forbid_overwrite is True
        with pytest.raises(FileExistsError):
            S.write_object('oss://bucket/data', b'y' * 100, overwrite=False)
    assert multipart.objects[('bucket', 'data')] == b'x' * 100
    assert multipart.events[-1] == 'abort'


def test_large_copy_streams_without_read_bytes_and_downloads_atomically(multipart, tmp_path, monkeypatch):
    source = tmp_path / 'source'
    source.write_bytes(bytes(range(100)))
    with S.io_session(oss_client=multipart):
        monkeypatch.setattr(S, 'read_bytes', lambda path: pytest.fail('whole-object read'))
        S.copy_file(source, 'oss://bucket/large')
        S.copy_file('oss://bucket/large', tmp_path / 'download')
        assert (tmp_path / 'download').read_bytes() == source.read_bytes()
        S.copy_file('oss://bucket/large', 'oss://bucket/copy')
        assert multipart.objects[('bucket', 'copy')] == source.read_bytes()


def test_remote_open_shares_multipart(multipart):
    with S.io_session(oss_client=multipart):
        with S.open_file('oss://bucket/data', 'wb') as stream:
            stream.write(b'x' * 100)
        assert multipart.events[-1] == 'complete'
        with pytest.raises(RuntimeError):
            with S.open_file('oss://bucket/data', 'wb') as stream:
                stream.write(b'y' * 100)
                raise RuntimeError('failed encoder')
        assert S.read_bytes('oss://bucket/data') == b'x' * 100
        with pytest.raises(ValueError, match='supports only'):
            with S.open_file('oss://bucket/data', 'ab'):
                pass


def test_nonseekable_copy_uses_bounded_sequential_upload(multipart):
    class NonSeekable:
        def __init__(self):
            self.stream = io.BytesIO(b'x' * 100)
        def read(self, size=-1):
            return self.stream.read(size)
    upload_function(multipart, oss.PutObjectRequest(bucket='bucket', key='data'), NonSeekable(), 100)
    assert multipart.options['parallel_num'] == 1
    assert multipart.objects[('bucket', 'data')] == b'x' * 100


def test_virtual_size_over_five_gib_never_uses_simple_put(monkeypatch):
    calls = []
    client = SimpleNamespace(put_object=lambda request: pytest.fail('oversized PutObject'))
    monkeypatch.setattr(oss, 'Uploader', lambda client, **kwargs: SimpleNamespace(
        upload_from=lambda request, reader: calls.append((kwargs, reader))))
    upload_function(client, oss.PutObjectRequest(bucket='bucket', key='large'), io.BytesIO(), (5 << 30) + 1)
    assert len(calls) == 1
    assert calls[0][0]['part_size'] <= 5 << 30
    with pytest.raises(ValueError, match='size limit'):
        upload_function(client, oss.PutObjectRequest(bucket='bucket', key='large'), io.BytesIO(), 1 << 60)
