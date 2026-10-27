"""Explicit local/OSS operations for shell entrypoints."""
import argparse
import json
from . import IO, copy_file, copy_tree


def main():
    parser = argparse.ArgumentParser(__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    cp = sub.add_parser('copy')
    cp.add_argument('source')
    cp.add_argument('destination')
    cp.add_argument('--recursive', action='store_true')
    inventory = sub.add_parser('inventory', help='Administrative listing, never a training input scan')
    inventory.add_argument('root')
    args = parser.parse_args()
    if args.command == 'copy':
        (copy_tree if args.recursive else copy_file)(args.source, args.destination)
    else:
        with IO() as storage:
            for row in storage.inventory(args.root, allow_remote=True):
                print(json.dumps(row), flush=True)


if __name__ == '__main__':
    main()
