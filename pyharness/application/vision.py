"""Authorized artifact bytes are materialized only at the normal model boundary.

Persistent messages contain artifact references, never base64 image payloads.
Capability is an explicit connection declaration, not a provider-specific guess.
"""
from __future__ import annotations

import base64
import zlib
import io
import warnings
from PIL import Image, UnidentifiedImageError
from pyharness.core.attachment import validate_image, reject_attachment
from pyharness.core.sandbox import fail


def preview_image(raw: bytes, filename: str) -> dict:
    info = validate_image(raw)
    extensions = {'image/png': {'.png'}, 'image/jpeg': {'.jpg', '.jpeg'},
                  'image/gif': {'.gif'}, 'image/webp': {'.webp'}}
    from pathlib import PurePosixPath
    if PurePosixPath(filename).suffix.lower() not in extensions[info['mime']]:
        reject_attachment('extension-mismatch')
    if info['w'] * info['h'] > 40_000_000:
        reject_attachment('pixel-limit')
    # Reject malformed containers before invoking the bounded pixel decoder.
    if info['mime'] == 'image/png':
        offset, kinds = 8, []
        while offset + 12 <= len(raw):
            length = int.from_bytes(raw[offset:offset+4], 'big')
            end = offset + 12 + length
            if end > len(raw): reject_attachment('truncated-image')
            kind = raw[offset+4:offset+8]
            checksum = int.from_bytes(raw[end-4:end], 'big')
            if zlib.crc32(raw[offset+4:end-4]) != checksum:
                reject_attachment('image-checksum')
            kinds.append(kind)
            offset = end
            if kind == b'IEND': break
        if offset != len(raw) or not kinds or kinds[0] != b'IHDR' or kinds[-1] != b'IEND' or b'IDAT' not in kinds:
            reject_attachment('truncated-image')
    elif info['mime'] == 'image/jpeg' and not raw.endswith(b'\xff\xd9'):
        reject_attachment('truncated-image')
    elif info['mime'] == 'image/gif' and not raw.endswith(b';'):
        reject_attachment('truncated-image')
    elif info['mime'] == 'image/webp' and int.from_bytes(raw[4:8], 'little') + 8 != len(raw):
        reject_attachment('truncated-image')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as decoded:
                if decoded.size != (info['w'], info['h']):
                    reject_attachment('image-dimension-mismatch')
                # Bound total decoded work across animation frames, not only
                # the first frame or the compressed upload size.
                frames = getattr(decoded, 'n_frames', 1)
                if frames > 100 or frames * info['w'] * info['h'] > 40_000_000:
                    reject_attachment('decoded-pixel-limit')
                for frame in range(frames):
                    decoded.seek(frame)
                    decoded.load()
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError,
            Image.DecompressionBombWarning, Image.DecompressionBombError):
        reject_attachment('image-decode-failed')
    return info


def require_connection(platform, connection):
    state = platform.service.tenant_state()  # public metadata, never secret resolution
    ident = connection or state['active']
    profile = next((p for p in state['profiles'] if p['id'] == ident), None)
    if not profile or profile.get('supports_vision') is not True:
        fail('vision_not_supported')
    if profile.get('fallback_models'):
        fail('vision_fallback_not_supported')
    return profile


async def authorized_images(platform, sid, ids):
    images = []
    for ident in ids:
        record, raw = await platform.artifact_content(ident)
        if record['session_id'] != sid:
            fail('artifact_owner_mismatch')
        if record['preview_type'] != 'image':
            continue
        info = preview_image(raw, record['filename'])
        if info['mime'] != record['media_type']:
            fail('artifact_integrity_failed')
        images.append((info['mime'], raw))
    if not images or len(images) > 5:
        fail('vision_image_count')
    if sum(len(raw) for _, raw in images) > 10 * 1024 * 1024:
        fail('vision_total_size')
    return images


def require_runtime(profile,runtime):
    identity=getattr(runtime,'model_identity',None)
    if (not identity or identity['id']!=profile['id'] or identity['model']!=profile['model']
            or identity['base_url']!=profile['base_url'] or identity['fallback_models']):
        fail('vision_runtime_restart_required')


async def model_messages(runtime, messages, ctx):
    events = list(ctx.session.events_after(0))
    users = [e for e in events if e.type == 'user.message']
    owned = next((e for e in users if
                  (e.payload.get('meta') or {}).get('platform', {}).get('task_id') == ctx.task_id), None)
    meta = (owned.payload.get('meta') or {}).get('platform', {}) if owned else {}
    if not meta.get('use_vision'):
        return messages
    profile = require_connection(runtime.platform, runtime.definition.model_connection)
    if profile['id'] != meta.get('vision_profile') or profile['model'] != meta.get('vision_model'):
        fail('vision_connection_changed')
    require_runtime(profile,runtime)
    images = await authorized_images(runtime.platform, ctx.session.sid, meta['artifact_ids'])
    result = [dict(m) for m in messages]
    positions = [i for i, message in enumerate(result) if message['role'] == 'user']
    trailing = sum(e.seq > owned.seq for e in users)
    if len(positions) <= trailing: fail('vision_message_missing')
    index = positions[-1-trailing]
    expected = owned.payload['content']
    for event in events:
        if event.type == 'user.message_edited' and event.payload.get('target_seq') == owned.seq:
            expected = event.payload['new_content']
    if result[index]['content'] != expected: fail('vision_message_missing')
    result[index]['content'] = [{'type': 'text', 'text': str(result[index]['content'])},
        *[{'type': 'image_url', 'image_url': {'url': 'data:'+mime+';base64,'+base64.b64encode(raw).decode('ascii')}}
          for mime, raw in images]]
    return result
