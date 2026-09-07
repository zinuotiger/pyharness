"""probe_engine_tools.py — engine 工具链装配探针。"""
import asyncio, sys, tempfile
from pathlib import Path
sys.path.insert(0, r'C:/Users/LENOVO/Desktop/mini-harness')
from pyharness.config import load_settings
from pyharness import engine


async def main():
    cfg = load_settings()
    tmp = Path(tempfile.mkdtemp(prefix='ph-eng-tool-'))
    ctx = await engine.assemble_real_engine(cfg, sid='tooltest-0001',
                                            sessions_dir=tmp)
    print('装配 OK')
    schemas = ctx.tools.schemas_for(ctx.scope)
    print('工具 schema 数:', len(schemas))
    for s in schemas:
        print(' -', s.get('name'), 'danger=', s.get('danger'))
    for n in ['fs.read_file', 'fs.write_file', 'fs.list_dir', 'fs.delete_file']:
        print(n, 'can_use=', ctx.scope.can_use(n))


if __name__ == '__main__':
    asyncio.run(main())
