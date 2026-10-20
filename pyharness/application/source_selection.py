"""Ephemeral metadata for explicitly selected names; never source contents."""
from pathlib import Path
import stat
from typing import Annotated

from pydantic import Field
from pyharness.application.source_preflight import PreflightGrant, check_root_metadata
from pyharness.core.sandbox import safe_path
from pyharness.errors import PyHError


class SelectionPreview(PreflightGrant):
    paths: list[Annotated[str, Field(min_length=1, max_length=1024)]] = Field(min_length=1, max_length=200)


def selection_name_reason(name,seen):
    """Pure name policy shared by explicit selection and metadata discovery."""
    parts=name.split('/')
    if (any(not part or part in {'.','..'} for part in parts)
            or any(ord(c)<32 or c in '\\:*?"<>|' for c in name)):
        return 'path_rejected'
    if any(p.lower().startswith('.env') or p.lower() in {
            '.git','.ssh','.aws','.azure','.gnupg','.kube','.npmrc','.netrc',
            'browser-profile','chrome-profile','edge-profile',
            'credentials.yaml','credentials.yml','credentials.json',
            'cookies.sqlite','cookies','login data','web data',
            'tokens.json','secrets.yml','secrets.yaml','secrets.json',
            'id_rsa','id_dsa','id_ecdsa','id_ed25519'}
            or p.lower().split('.')[0] in {'credentials','secrets','secret','token','tokens','api_key','apikey'}
            or p.lower().endswith(('.pem','.key','.p12','.pfx','.kdbx')) for p in parts):
        return 'sensitive_name'
    if name.casefold() in seen:return 'duplicate_path'
    return None


def observe_selection(row, command):
    result = check_root_metadata(row)
    result.pop('root_identity')
    root = Path(row['grant']['canonical_root'])
    items, seen, total = [], set(), 0
    for index, name in enumerate(command.paths):
        reason = selection_name_reason(name,seen)
        if reason is None:
            seen.add(name.casefold())
            try:
                info = safe_path(root, name).lstat()
                if not stat.S_ISREG(info.st_mode):
                    reason = 'not_regular'
                elif info.st_size > 10 * 1024 * 1024:
                    reason = 'file_too_large'
                elif total + info.st_size > 10 * 1024 * 1024:
                    reason = 'selection_size_limit'
            except PyHError:
                reason = 'path_rejected'
            except (OSError, ValueError):
                reason = 'file_unavailable'
        if reason:
            items.append({'input_index': index, 'status': 'excluded', 'reason': reason})
            continue
        total += info.st_size
        items.append({'input_index': index, 'status': 'accepted', 'path': name,
                      'size_bytes': info.st_size, 'mtime_ns': str(info.st_mtime_ns)})
    accepted = sum(item['status'] == 'accepted' for item in items)
    return {**result, 'status': 'metadata_observed', 'items': items,
            'summary': {'requested': len(items), 'accepted': accepted, 'excluded': len(items) - accepted,
                        'total_size_bytes': total}}
