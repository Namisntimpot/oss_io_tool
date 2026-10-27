"""GLB and glTF with external buffers through the resolver. Install .[mesh]."""
import argparse
import trimesh

from oss_io_tool import Path, io_session, load_trimesh, write_bytes


def main(root):
    root = Path(root)
    scene = trimesh.Scene(trimesh.creation.box())
    with io_session():
        write_bytes(root / 'box.glb', scene.export(file_type='glb'))
        assert len(load_trimesh(root / 'box.glb', force='scene', process=False).geometry) == 1
        bundle = scene.export(file_type='gltf')
        for name, content in bundle.items():
            write_bytes(root / 'gltf' / name, content)
        filename = next(name for name in bundle if name.endswith('.gltf'))
        assert len(load_trimesh(root / 'gltf' / filename, force='scene', process=False).geometry) == 1
    print(f'GLB/glTF IO passed: {root}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--root', default='/tmp/oss-io-example/meshes')
    main(parser.parse_args().root)
