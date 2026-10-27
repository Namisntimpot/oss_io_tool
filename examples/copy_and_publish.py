"""Copy local files/trees, publish a package, and drain bounded background artifact uploads."""
import argparse
import json
import tempfile
from pathlib import Path as LocalPath

from oss_io_tool import Path, copy_file, copy_tree, io_session, native_package, publish_package, read_bytes
from oss_io_tool.publisher import ArtifactPublisher


def main(root):
    root = Path(root)
    with tempfile.TemporaryDirectory(prefix='oss-io-source-') as directory, io_session():
        stage = LocalPath(directory)
        model = stage / 'model'
        model.mkdir()
        (model / 'config.json').write_text('{"version":1}')
        (model / 'weights.bin').write_bytes(b'weights')
        (model / 'ignore.tmp').write_bytes(b'ignored')
        copy_file(model / 'weights.bin', root / 'single.bin')
        assert read_bytes(root / 'single.bin') == b'weights'
        copy_tree(model, root / 'tree', exclude=('ignore.tmp',))
        (model / 'ignore.tmp').unlink()
        publish_package(model, root / 'package')
        with native_package(root / 'package') as local:
            assert json.loads((LocalPath(local) / 'config.json').read_text()) == {'version': 1}
            assert (LocalPath(local) / 'weights.bin').read_bytes() == b'weights'

        checkpoint = stage / 'checkpoint.bin'
        checkpoint.write_bytes(b'checkpoint')
        logs = stage / 'logs'
        logs.mkdir()
        (logs / 'train.log').write_text('step=1\n')
        published = []
        publisher = ArtifactPublisher(max_pending=2)
        try:
            publisher.submit(checkpoint, root / 'checkpoint.bin',
                             on_published=lambda: published.append(True))
            publisher.sync_tree_coalesced(logs, root / 'logs')
        finally:
            publisher.drain()  # Must finish before temporary staged sources disappear.
        assert published == [True]
        assert not checkpoint.exists()  # Default remove_source=True after successful publication.
        assert read_bytes(root / 'checkpoint.bin') == b'checkpoint'
        assert (logs / 'train.log').exists()  # Log-tree sync keeps its source.
    print(f'Copy/package/artifact publication passed: {root}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--root', default='/tmp/oss-io-example/publication')
    main(parser.parse_args().root)
