"""Concurrent reads, bounded batches and IO-session propagation to worker threads."""
import argparse
from concurrent.futures import ThreadPoolExecutor

from oss_io_tool import Path, current_io, io_session, read_text, submit_io, wait_futures, write_text


def main(root):
    root = Path(root)
    with io_session(workers=2) as storage:
        # Bound retained payloads by submitting one batch at a time.
        for start in range(0, 8, 4):
            writes = [write_text(root / f'{i}.txt', str(i), asynchronous=True)
                      for i in range(start, start + 4)]
            wait_futures(writes)
        with ThreadPoolExecutor(max_workers=3) as pool:
            assert submit_io(pool, current_io).result() is storage
            reads = [submit_io(pool, read_text, root / f'{i}.txt') for i in range(8)]
            wait_futures(reads)
            assert [future.result() for future in reads] == [str(i) for i in range(8)]
        storage.flush()
    print(f'Threaded IO passed: {root}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--root', default='/tmp/oss-io-example/threads')
    main(parser.parse_args().root)
