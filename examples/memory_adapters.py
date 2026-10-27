"""Adapt third-party file-object and filename APIs using memory, without disk staging."""
import argparse
import io
import subprocess
import sys
import zipfile
from pathlib import Path as LocalPath
from oss_io_tool import (Path, io_session, memory_file, memory_directory,
                         native_directory, read_bytes, write_bytes)


def main(root):
    root = Path(root)
    with io_session():
        # zipfile is a real third-party-style API accepting fileobj.
        with io.BytesIO() as stream:
            with zipfile.ZipFile(stream, 'w') as archive:
                archive.writestr('hello.txt', b'hello from memory')
            write_bytes(root / 'archive.zip', stream.getvalue())
        with io.BytesIO(read_bytes(root / 'archive.zip')) as stream:
            with zipfile.ZipFile(stream) as archive:
                assert archive.read('hello.txt') == b'hello from memory'

        # Native filename API, still in the same process.
        with memory_file(read_bytes(root / 'archive.zip'), name='archive.zip') as filename:
            with zipfile.ZipFile(filename) as archive:
                assert archive.read('hello.txt') == b'hello from memory'

        # A subprocess needs real tmpfs paths, not the parent's /proc/self/fd/N.
        with memory_directory() as directory:
            local = LocalPath(directory)
            (local / 'input.zip').write_bytes(read_bytes(root / 'archive.zip'))
            subprocess.run([
                sys.executable, '-c',
                'import pathlib,sys,zipfile; '
                'p=pathlib.Path(sys.argv[1]); '
                '(p/"output.txt").write_bytes(zipfile.ZipFile(p/"input.zip").read("hello.txt"))',
                directory,
            ], check=True)
            write_bytes(root / 'output.txt', (local / 'output.txt').read_bytes())

        # Known input filenames for a loader that opens siblings itself.
        with native_directory(root, ['output.txt', 'archive.zip']) as directory:
            assert (LocalPath(directory) / 'output.txt').read_bytes() == b'hello from memory'
    print(f'BytesIO/memfd/tmpfs roundtrips passed: {root}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--root', default='/tmp/oss-io-example/memory')
    main(parser.parse_args().root)
