"""PyTorch, safetensors and optional Linux mmap. Install .[torch]."""
import argparse
import torch
from safetensors.torch import save as safetensors_save_bytes

from oss_io_tool import (Path, io_session, safetensors_load, torch_load,
                         torch_save, write_bytes)


def main(root, mmap=False):
    root = Path(root)
    value = {'weight': torch.arange(12, dtype=torch.float32).reshape(3, 4)}
    with io_session():
        future = torch_save(value, root / 'weights.pt', asynchronous=True)
        future.result()
        result = torch_load(root / 'weights.pt', map_location='cpu', weights_only=True)
        torch.testing.assert_close(result['weight'], value['weight'])
        write_bytes(root / 'weights.safetensors', safetensors_save_bytes(value))
        torch.testing.assert_close(safetensors_load(root / 'weights.safetensors')['weight'], value['weight'])
        if mmap:
            result = torch_load(root / 'weights.pt', map_location='cpu', weights_only=True, mmap=True)
            torch.testing.assert_close(result['weight'], value['weight'])
    print(f'PyTorch/safetensors IO passed: {root}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--root', default='/tmp/oss-io-example/checkpoints')
    parser.add_argument('--mmap', action='store_true', help='Remote mmap needs Linux memfd')
    args = parser.parse_args()
    main(args.root, mmap=args.mmap)
