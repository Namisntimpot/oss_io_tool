import asyncio
import importlib
import threading
from pathlib import Path

import pytest
import oss_io_tool as S


def test_compatibility_imports_share_module_session_and_submodules():
    import nsl_io
    import nsl_io.publisher
    import oss_io_tool.publisher
    assert nsl_io is S
    assert importlib.import_module('nsl_io.publisher') is oss_io_tool.publisher
    assert importlib.import_module('nsl_io.index') is importlib.import_module('oss_io_tool.index')
    with S.io_session() as storage:
        assert nsl_io.current_io() is storage


def test_asyncio_roundtrip_and_context_without_blocking_event_loop(tmp_path, monkeypatch):
    async def main():
        with S.io_session(workers=2) as storage:
            original = storage.read_bytes
            entered, release = threading.Event(), threading.Event()
            def slow_read(path):
                entered.set()
                assert release.wait(5)
                assert S.current_io() is storage
                return original(path)
            monkeypatch.setattr(storage, 'read_bytes', slow_read)
            path = tmp_path / 'file'
            await S.async_write_text(path, '异步')
            read = asyncio.create_task(S.async_read_text(path))
            try:
                assert await asyncio.to_thread(entered.wait, 5)
                assert not read.done()
            finally:
                release.set()
            assert await read == '异步'
            await S.async_flush()
    asyncio.run(main())


def test_cancelled_async_write_remains_tracked_until_flush(tmp_path, monkeypatch):
    async def main():
        with S.io_session() as storage:
            original = storage._put
            entered, release = threading.Event(), threading.Event()
            def slow_put(path, data):
                entered.set()
                assert release.wait(5)
                original(path, data)
            monkeypatch.setattr(storage, '_put', slow_put)
            path = tmp_path / 'file'
            task = asyncio.create_task(S.async_write_bytes(path, b'payload'))
            try:
                assert await asyncio.to_thread(entered.wait, 5)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            finally:
                release.set()
            await S.async_flush()
            assert path.read_bytes() == b'payload'
    asyncio.run(main())


def test_native_output_and_tmpfs_subprocess_lifetime():
    import subprocess
    import sys
    with S.memory_file(name='output.bin') as filename:
        Path(filename).write_bytes(b'native output')
        assert Path(filename).read_bytes() == b'native output'
    assert not Path(filename).exists()
    with S.memory_directory() as directory:
        path = Path(directory) / 'native.bin'
        subprocess.run([sys.executable, '-c',
                        'import pathlib,sys; pathlib.Path(sys.argv[1]).write_bytes(b"child")', str(path)], check=True)
        assert path.read_bytes() == b'child'
    assert not Path(directory).exists()
