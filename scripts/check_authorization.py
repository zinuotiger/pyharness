"""Count lexical governance authorization calls, including simple assignment aliases.

Aliases are conservatively tracked per file (including branches); reflection,
dynamic getattr and cross-module data flow are outside this static gate. This
is an architectural regression check, not a runtime security boundary.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path


def scan(root=None):
    root = Path(root) if root is not None else Path(__file__).resolve().parents[1]
    sites = []
    for path in sorted((root/'pyharness').rglob('*.py')):
        source = path.read_text(encoding='utf-8')
        tree = ast.parse(source, filename=str(path))
        aliases = {}
        def kind(node):
            if isinstance(node, ast.Name): return aliases.get(node.id)
            if isinstance(node, ast.Attribute):
                if node.attr == 'governance': return 'governance'
                if node.attr == 'authorize' and kind(node.value) == 'governance': return 'authorize'
            return None
        assignments = [n for n in ast.walk(tree) if isinstance(n, (ast.Assign, ast.AnnAssign))]
        for _ in range(len(assignments)+1):
            changed = False
            for node in assignments:
                value = kind(node.value)
                for target in node.targets if isinstance(node, ast.Assign) else [node.target]:
                    if isinstance(target, ast.Name) and value and aliases.get(target.id) != value:
                        aliases[target.id] = value
                        changed = True
            if not changed: break
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and kind(node.func) == 'authorize':
                sites.append({'file':path.relative_to(root).as_posix(), 'line':node.lineno,
                              'column':node.col_offset, 'expression':ast.get_source_segment(source,node)})
    return sorted(sites,key=lambda s:(s['file'],s['line'],s['column']))


def main():
    sites = scan()
    print(json.dumps(sites, ensure_ascii=False, indent=2))
    return 0 if len(sites) == 1 and sites[0]['file'] == 'pyharness/core/tools_executor.py' else 1


if __name__ == '__main__': raise SystemExit(main())
