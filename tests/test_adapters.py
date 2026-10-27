import io
import json
import numpy as np
import pytest
from oss_io_tool import (cv2_read, cv2_write, imageio_read, imageio_write,
    io_session, numpy_load, publish_package, native_package, read_bytes, write_bytes)


class Sink:
    def __init__(self):
        self.files = {}
    def matches(self, path):
        return str(path).startswith('oss://')
    def read(self, path):
        return self.files[str(path)]
    def write(self, path, data):
        self.files[str(path)] = bytes(data)


def test_image_and_numpy_cloud_roundtrips():
    pytest.importorskip('imageio')
    image = np.arange(8*9*3, dtype=np.uint8).reshape(8, 9, 3)
    with io_session(write_sink=Sink()):
        imageio_write('oss://test/image.png', image)
        np.testing.assert_array_equal(imageio_read('oss://test/image.png'), image)
        assert cv2_write('oss://test/cv.png', image)
        np.testing.assert_array_equal(cv2_read('oss://test/cv.png'), image)
        stream = io.BytesIO()
        np.save(stream, image)
        write_bytes('oss://test/image.npy', stream.getvalue())
        np.testing.assert_array_equal(numpy_load('oss://test/image.npy'), image)
        mapped = numpy_load('oss://test/image.npy', mmap_mode='r')
        np.testing.assert_array_equal(mapped, image)


def test_package_inventory_and_implicit_native_reads(tmp_path):
    (tmp_path / 'config.json').write_text('{}')
    (tmp_path / 'weights.bin').write_bytes(b'weights')
    with io_session(write_sink=Sink()):
        publish_package(tmp_path, 'oss://test/package')
        assert set(json.loads(read_bytes('oss://test/package/_io_files.json'))) == {'config.json', 'weights.bin'}
        with native_package('oss://test/package') as directory:
            from pathlib import Path
            assert (Path(directory) / 'weights.bin').read_bytes() == b'weights'


def test_torch_remote_mmap_survives_native_descriptor_close():
    torch = pytest.importorskip('torch')
    from oss_io_tool import torch_load, torch_save
    with io_session(write_sink=Sink()):
        value = {'weight': torch.arange(32)}
        torch_save(value, 'oss://test/checkpoint.pt')
        result = torch_load('oss://test/checkpoint.pt', mmap=True, weights_only=True)
        torch.testing.assert_close(result['weight'], value['weight'])
