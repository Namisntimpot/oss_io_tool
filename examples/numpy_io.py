"""NumPy .npy/.npz, raw binary arrays, BytesIO and optional Linux mmap."""
import argparse
import io

import numpy as np
from oss_io_tool import Path, io_session, numpy_load, open_file, read_array, write_bytes


def main(root, mmap=False):
    root = Path(root)
    values = np.arange(12, dtype='<f4').reshape(3, 4)
    with io_session():
        with open_file(root / 'values.npy', 'wb') as stream:
            np.save(stream, values, allow_pickle=False)
        np.testing.assert_array_equal(numpy_load(root / 'values.npy', allow_pickle=False), values)
        with io.BytesIO() as stream:
            np.savez_compressed(stream, values=values, labels=np.arange(3))
            write_bytes(root / 'bundle.npz', stream.getvalue())
        # NpzFile is lazy: read its members before closing the stream/archive.
        with open_file(root / 'bundle.npz', 'rb') as stream:
            with np.load(stream, allow_pickle=False) as archive:
                np.testing.assert_array_equal(archive['values'], values)
        write_bytes(root / 'values.raw', values.tobytes())
        with open_file(root / 'values.raw', 'rb') as stream:
            result = read_array(stream, '<f4', count=values.size).reshape(values.shape)
            np.testing.assert_array_equal(result, values)
        if mmap:
            mapped = numpy_load(root / 'values.npy', mmap_mode='r', allow_pickle=False)
            np.testing.assert_array_equal(mapped, values)
    print(f'NumPy IO passed: {root}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--root', default='/tmp/oss-io-example/numpy')
    parser.add_argument('--mmap', action='store_true', help='Use Linux memfd for .npy mmap')
    args = parser.parse_args()
    main(args.root, mmap=args.mmap)
