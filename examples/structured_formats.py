"""JSON, JSONL, CSV, pickle, optional YAML, gzip, ZIP and TAR through the same storage API."""
import argparse
import csv
import gzip
import io
import json
import pickle
import tarfile
import zipfile

from oss_io_tool import Path, io_session, open_file, read_bytes, read_text, write_bytes, write_text


def main(root, with_yaml=False):
    root = Path(root)
    records = [{'id': 1, 'name': '样本'}]
    with io_session():
        write_text(root / 'config.json', json.dumps(records, ensure_ascii=False))
        assert json.loads(read_text(root / 'config.json')) == records
        # This pickle was generated here; only deserialize trusted pickle inputs.
        write_bytes(root / 'records.pkl', pickle.dumps(records))
        assert pickle.loads(read_bytes(root / 'records.pkl')) == records
        if with_yaml:
            import yaml
            write_text(root / 'config.yaml', yaml.safe_dump(records, allow_unicode=True))
            assert yaml.safe_load(read_text(root / 'config.yaml')) == records

        # Remote append is unsupported: write a complete JSONL/CSV object.
        with open_file(root / 'records.jsonl', 'w') as stream:
            for record in records:
                stream.write(json.dumps(record, ensure_ascii=False) + '\n')
        with open_file(root / 'records.jsonl', 'r') as stream:
            assert [json.loads(line) for line in stream] == records
        with open_file(root / 'records.csv', 'w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=['id', 'name'])
            writer.writeheader()
            writer.writerows(records)
        with open_file(root / 'records.csv', 'r', newline='') as stream:
            assert next(csv.DictReader(stream))['name'] == '样本'

        payload = json.dumps(records, ensure_ascii=False).encode('utf-8')
        write_bytes(root / 'records.json.gz', gzip.compress(payload))
        assert json.loads(gzip.decompress(read_bytes(root / 'records.json.gz'))) == records
        # Streaming format wrapper; remote open_file still buffers the object.
        with open_file(root / 'wrapped.gz', 'wb') as raw:
            with gzip.GzipFile(fileobj=raw, mode='wb') as compressed:
                compressed.write(payload)
        with open_file(root / 'wrapped.gz', 'rb') as raw:
            with gzip.GzipFile(fileobj=raw, mode='rb') as compressed:
                assert compressed.read() == payload

        with open_file(root / 'archive.zip', 'wb') as raw:
            with zipfile.ZipFile(raw, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr('records.json', payload)
        with open_file(root / 'archive.zip', 'rb') as raw:
            with zipfile.ZipFile(raw) as archive:
                assert archive.read('records.json') == payload

        with open_file(root / 'archive.tar.gz', 'wb') as raw:
            with tarfile.open(fileobj=raw, mode='w:gz') as archive:
                member = tarfile.TarInfo('records.json')
                member.size = len(payload)
                archive.addfile(member, io.BytesIO(payload))
        with open_file(root / 'archive.tar.gz', 'rb') as raw:
            with tarfile.open(fileobj=raw, mode='r:gz') as archive:
                with archive.extractfile('records.json') as member:
                    assert member.read() == payload
    print(f'Structured/compressed file roundtrips passed: {root}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--root', default='/tmp/oss-io-example/formats')
    parser.add_argument('--yaml', action='store_true', help='Also demonstrate PyYAML safe_load/safe_dump')
    args = parser.parse_args()
    main(args.root, with_yaml=args.yaml)
