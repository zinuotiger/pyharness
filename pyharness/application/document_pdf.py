"""Owned-process PDF extraction with bounded time, output and memory; never reused."""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from pyharness.core.process_owner import ProcessOwner
from pyharness.application.document_text import parser_version, text_blocks
from pyharness.application.source_snapshot import checked_private_bytes
from pyharness.core.tenant_settings import TenantSettingsStore
from pyharness.errors import raise_code
from pyharness.core.sandbox import safe_path


def _extract(root, raw):
    worker = Path(__file__).with_name('document_pdf_worker.py')
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    safe_path(root, 'parser-check')
    with tempfile.TemporaryDirectory(prefix='parse-', dir=root) as owned:
        safe_path(root, Path(owned).name)
        path = safe_path(Path(owned), 'source.pdf')
        TenantSettingsStore._atomic_write(path, raw)
        if checked_private_bytes(path, limit=2 * 1024**2) != raw:
            raise_code('PERS-202', reason='document_parser_input_unconfirmed')
        env = {k: v for k, v in os.environ.items() if k.upper() in {'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP'}}
        try:
            owner = ProcessOwner([sys.executable, '-I', '-B', str(worker), str(path)], cwd=owned, env=env)
        except (OSError, RuntimeError):
            raise_code('EVT-104', reason='document_parser_unavailable')
        try:
            result = owner.collect(timeout=10)
        finally:
            owner.terminate()
        if not result['ok']:
            raise_code('EVT-104', reason='document_parser_unavailable')
        try:
            value = json.loads(result['stdout'])
            if not isinstance(value, dict):
                raise ValueError()
            if value.get('rejected'):
                raise_code('GRD-403', reason=value['rejected'])
            pages = value['pages']
            if not isinstance(pages, list) or len(pages) > 200 or any(not isinstance(p, str) for p in pages):
                raise ValueError()
            blocks = text_blocks(pages, pdf=True)
            return {'format': 'pdf', 'parser_version': parser_version('pdf'), 'page_count': len(pages),
                    'unlocatable_pages': [i+1 for i, t in enumerate(pages) if not t.strip()],
                    'blocks': blocks, 'capability': 'locatable' if blocks else 'unlocatable',
                    'capability_reason': value['capability_reason']}
        except (ValueError, KeyError):
            raise_code('PERS-202', reason='document_parser_output_invalid')


async def parse_pdf(root, raw):
    if len(raw) > 2 * 1024**2:
        raise_code('EVT-100', reason='document_pdf_size_limit')
    # Service admission waits for this bounded disposable carrier even on cancellation.
    task = asyncio.create_task(asyncio.to_thread(_extract, root, raw))
    try:
        return await asyncio.shield(task)
    except (OSError, RuntimeError):
        raise_code('PERS-202', reason='document_parser_storage_unavailable')
    except asyncio.CancelledError:
        try:
            await asyncio.shield(task)
        finally:
            raise
