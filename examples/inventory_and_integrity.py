"""Local/OSS inventory, indexed discovery, byte ranges, pinned reads and create-only writes."""
import argparse
import hashlib
import json
import uuid

from oss_io_tool import Path, io_session, read_bytes, read_pinned, write_bytes, write_object, write_text
from oss_io_tool.index import IndexedFilesystem


def main(root):
    root = Path(root)
    source = root / 'inputs'
    payload = b'HEADER:payload:END'
    with io_session(cache_reads=True, record_writes=True, max_remote_read_bytes=1 << 20) as storage:
        write_bytes(source / 'record.bin', payload)
        write_text(source / 'configs/model.json', '{}')
        assert (source / 'record.bin').exists()
        assert (source / 'record.bin').is_file()
        assert storage.stat(source / 'record.bin').st_size == len(payload)
        assert storage.read_range(source / 'record.bin', 7, 7) == b'payload'
        assert read_bytes(source / 'record.bin') == payload
        assert read_bytes(source / 'record.bin') == payload

        # Explicit administrative listing; S3 inventory is not implemented.
        rows = [row for row in storage.inventory(source, allow_remote=True)
                if str(Path(row['path']).relative_to(source)) != '_io_files.json']
        assert {str(Path(row['path']).relative_to(source)) for row in rows} == {
            'record.bin', 'configs/model.json'}
        inventory = root / 'inventory.jsonl'
        write_text(inventory, ''.join(json.dumps(row) + '\n' for row in rows))
        names = sorted(str(Path(row['path']).relative_to(source)) for row in rows)
        write_text(source / '_io_files.json', json.dumps(names))
        indexed = IndexedFilesystem(source)
        assert indexed.smart_listdir(source / 'configs') == ['model.json']
        assert indexed.smart_glob(source / 'configs/*.json') == [str(source / 'configs/model.json')]
        with indexed.smart_open(source / 'record.bin', 'rb') as stream:
            assert stream.read(7) == b'HEADER:'
        assert indexed.smart_exists(source / 'record.bin')

        # OSS uses the object's ETag as If-Match; local uses SHA-256 content.
        target = source / 'record.bin'
        expected = (storage.stat(target).etag if str(target).startswith('oss://')
                    else hashlib.sha256(payload).hexdigest())
        assert read_pinned(target, etag=expected) == payload
        claim = root / 'claims' / (uuid.uuid4().hex + '.json')
        write_object(claim, b'{"owner":"example"}', overwrite=False,
                     metadata={'purpose': 'example'})
        try:
            write_object(claim, b'overwrite', overwrite=False)
        except FileExistsError:
            pass
        else:
            raise AssertionError('create-only write unexpectedly replaced the object')
        claim.unlink()
        storage.remove(root / 'absent-object', missing_ok=True)
        print('IO statistics before session cleanup:', {
            'bytes_read': storage.bytes_read, 'remote_bytes_read': storage.remote_bytes_read,
            'cache_hits': storage.cache_hits, 'writes': dict(storage.written_objects)})
    print(f'Inventory/range/integrity examples passed: {root}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--root', default='/tmp/oss-io-example/inventory', help='Local path or oss:// prefix; S3 listing is unsupported')
    args = parser.parse_args()
    if args.root.startswith('s3://'):
        parser.error('This administrative example needs local/OSS inventory and create-only writes.')
    main(args.root)
