"""Run after installing the package. --root accepts local, oss:// or s3://."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from oss_io_tool import Path, copy_file, io_session, read_text, submit_io, write_text


def main(root):
    root = Path(root)
    with io_session(workers=2) as storage:
        write_text(root / 'sync.txt', '同步读写')
        assert read_text(root / 'sync.txt') == '同步读写'
        future = write_text(root / 'background.txt', '后台写入', asynchronous=True)
        future.result()
        with ThreadPoolExecutor(max_workers=2) as pool:
            assert submit_io(pool, read_text, root / 'background.txt').result() == '后台写入'
        copy_file(root / 'sync.txt', root / 'copy.txt')
        storage.flush()
        write_text(root / '_DONE', '1')
    print(f'Sync/background roundtrip passed: {root}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--root', default='/tmp/oss-io-example/basic')
    main(parser.parse_args().root)
