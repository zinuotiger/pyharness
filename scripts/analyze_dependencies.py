"""Reproducible module import inventory; separate eager and deferred dependencies."""
from __future__ import annotations
import ast
import json
from pathlib import Path
import subprocess
import io
import tarfile

ROOT = Path(__file__).resolve().parents[1]


def analyze(ref=None):
    if ref:
        archive = subprocess.check_output(['git','archive',ref,'pyharness'],cwd=ROOT)
        with tarfile.open(fileobj=io.BytesIO(archive)) as files:
            sources = {ROOT/member.name:files.extractfile(member).read().decode('utf-8')
                       for member in files.getmembers() if member.isfile() and member.name.endswith('.py')}
    else:
        sources = {path:path.read_text(encoding='utf-8') for path in (ROOT/'pyharness').rglob('*.py')}
    modules = {'.'.join(path.relative_to(ROOT).with_suffix('').parts).removesuffix('.__init__'):path for path in sources}
    edges, eager = {}, {}
    for name,path in modules.items():
        tree = ast.parse(sources[path])
        top = {id(n) for n in tree.body if isinstance(n,(ast.Import,ast.ImportFrom))}
        edges[name],eager[name] = set(),set()
        for node in ast.walk(tree):
            targets = []
            if isinstance(node,ast.Import):
                targets = [a.name for a in node.names]
            elif isinstance(node,ast.ImportFrom):
                base = node.module or ''
                if node.level:
                    package = name if path.name=='__init__.py' else name.rsplit('.',1)[0]
                    base = '.'.join([*package.split('.')[:len(package.split('.'))-node.level+1],base]).rstrip('.')
                targets = [base+'.'+a.name if base+'.'+a.name in modules else base for a in node.names]
            for target in targets:
                if target in modules and target!=name:
                    edges[name].add(target)
                    if id(node) in top:
                        eager[name].add(target)
    def cycles(graph):
        index,low,stack,active,found = {},{},[],set(),[]
        def visit(node):
            index[node]=low[node]=len(index)
            stack.append(node);active.add(node)
            for target in graph[node]:
                if target not in index:
                    visit(target);low[node]=min(low[node],low[target])
                elif target in active:
                    low[node]=min(low[node],index[target])
            if low[node]==index[node]:
                group=[]
                while True:
                    value=stack.pop();active.remove(value);group.append(value)
                    if value==node:break
                if len(group)>1:found.append(sorted(group))
        for node in graph:
            if node not in index:visit(node)
        return sorted(found)
    sizes={}
    for filename in ('pyharness/application/service.py','pyharness/engine.py'):
        before=subprocess.check_output(['git','show','7b1bdeab:'+filename],cwd=ROOT).decode('utf-8')
        after=(ROOT/filename).read_text(encoding='utf-8')
        sizes[filename]={'before_lines':len(before.splitlines()),'after_lines':len(after.splitlines()),
                         'before_any_mentions':before.count('Any'),'after_any_mentions':after.count('Any')}
    return {'module_count':len(modules),'eager_cycles':cycles(eager),'all_import_cycles':cycles(edges),
        'sizes':sizes,'new_boundaries':{n:sorted(edges[n]) for n in modules if n.startswith('pyharness.integrations.')
          or n in {'pyharness.core.task_runner','pyharness.application.execution_config','pyharness.application.codex_runner',
                   'pyharness.application.channels','pyharness.application.wechat_channel'}},
        'engine_to_application':[n for n in edges['pyharness.engine'] if n.startswith('pyharness.application')]}


if __name__=='__main__':
    current, baseline = analyze(), analyze('7b1bdeab')
    current['baseline'] = {key:baseline[key] for key in ('module_count','eager_cycles','all_import_cycles','engine_to_application')}
    print(json.dumps(current,ensure_ascii=False,indent=2))
