"""asyncio roundtrips without blocking the event loop."""
import argparse
import asyncio
from oss_io_tool import (Path, io_session, async_read_bytes, async_read_text,
                         async_write_bytes, async_write_text, async_flush)


async def main(root):
    root = Path(root)
    with io_session(workers=2):
        await asyncio.gather(*(async_write_text(root / f'{i}.txt', str(i)) for i in range(4)))
        values = await asyncio.gather(*(async_read_text(root / f'{i}.txt') for i in range(4)))
        assert values == ['0', '1', '2', '3']
        await async_write_bytes(root / 'binary.bin', b'async bytes')
        assert await async_read_bytes(root / 'binary.bin') == b'async bytes'
        await async_flush()
        await async_write_text(root / '_DONE', '1')
    print(f'Asyncio roundtrip passed: {root}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--root', default='/tmp/oss-io-example/asyncio')
    asyncio.run(main(parser.parse_args().root))
