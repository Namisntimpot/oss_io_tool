"""Explicit filename inventory for legacy packages that require directory APIs."""
import json
import os
from collections import defaultdict

from . import Path, _remote, _glob_matches, current_io, open_file


class IndexedFilesystem:
    def __init__(self, root, inventory=None):
        self.root = Path(root)
        self.files = None
        self.children = defaultdict(set)
        if _remote(root):
            self.files = frozenset(json.loads(Path(inventory or self.root / '_io_files.json').read_text()))
            for name in self.files:
                if not isinstance(name, str) or name.startswith('/') or '..' in name.split('/'):
                    raise ValueError('invalid relative inventory path')
                parts = name.split('/')
                for i, part in enumerate(parts):
                    self.children['/'.join(parts[:i])].add(part)

    smart_open = staticmethod(open_file)

    def smart_exists(self, path):
        return current_io().exists(path)

    def smart_listdir(self, path):
        if self.files is None:
            return [p.name for p in Path(path).iterdir()]
        key = str(Path(path).relative_to(self.root))
        if key == '.':
            key = ''
        return sorted(self.children[key])

    def smart_glob(self, pattern):
        if self.files is None:
            import glob
            return glob.glob(os.fspath(pattern))
        relative = str(Path(pattern).relative_to(self.root))
        parent, _, leaf = relative.rpartition('/')
        if not any(char in parent for char in '*?[') and '**' not in leaf:
            names = (parent + '/' + name if parent else name for name in self.children[parent])
        else:
            names = self.files
        return [str(self.root / name) for name in sorted(names) if _glob_matches(name, relative)]
