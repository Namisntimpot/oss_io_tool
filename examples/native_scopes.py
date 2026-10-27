"""Scope native input paths with memfd, a decorator, or an explicit disk fallback."""
import argparse
from pathlib import Path as LocalPath

from oss_io_tool import (Path, io_session, local_file, native_file_scope, native_files,
                         native_path, read_bytes, write_bytes)


@native_files
def load_with_filename(path):
    # Replace LocalPath.read_bytes with a native library that opens its own path.
    filename = native_path(path)
    return LocalPath(filename).read_bytes()


def main(root):
    root = Path(root)
    with io_session():
        write_bytes(root / 'input.bin', b'native input')
        assert load_with_filename(root / 'input.bin') == b'native input'
        with native_file_scope():
            first = native_path(root / 'input.bin')
            second = native_path('prepared.bin', data=b'already in memory')
            assert LocalPath(first).read_bytes() == b'native input'
            assert LocalPath(second).read_bytes() == b'already in memory'
        assert not LocalPath(first).exists() and not LocalPath(second).exists()
        with native_file_scope():
            # Explicit opt-in: this fallback writes temporary disk files.
            filename = native_path(root / 'input.bin', memory=False)
            assert LocalPath(filename).suffix == '.bin'
        assert not LocalPath(filename).exists()
        with local_file(read_bytes(root / 'input.bin'), name='input.bin') as filename:
            assert LocalPath(filename).read_bytes() == b'native input'
    print(f'Native scopes/decorator/disk fallback passed: {root}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--root', default='/tmp/oss-io-example/scopes')
    main(parser.parse_args().root)
