"""Non-sensitive host preference adapter; no account/model/runtime dependencies."""
from __future__ import annotations
import json
import math
import os
import tempfile
from pathlib import Path

DEFAULT = dict(schemaVersion=1, themeId='tech-blue-particle', backgroundId=None, animationMode='normal',
               count=48, speed=.35, links=True, opacity=.55, pixelRatio=1.5)


def normalize(value, *, allow_scenes=False):
    value = value if isinstance(value, dict) else {}
    result = dict(DEFAULT)
    # Web/WebView static backgrounds share this same preference file. Native
    # normalization deliberately keeps its existing theme-only boundary.
    backgrounds = ('particle-nebula-light', 'particle-ice-aurora-light',
                   'particle-ocean-blue-light', 'particle-starry-sky-light',
                   'particle-aurora-light', 'particle-ocean-light')
    if allow_scenes and value.get('backgroundId') in backgrounds:
        result['backgroundId'] = value['backgroundId']
    themes = ('light','dark','tech-blue-particle')
    if allow_scenes: themes += ('starfield','sakura','aurora','gold','ocean')
    for key, choices in [('themeId', themes),
                         ('animationMode', ('normal','low','off'))]:
        if value.get(key) in choices: result[key] = value[key]
    for key, low, high in [('count',0,80),('speed',0,1),('opacity',0,1),('pixelRatio',1,2)]:
        number = value.get(key)
        if type(number) in (int, float) and math.isfinite(number):
            result[key] = min(high, max(low, number))
    result['count'] = int(math.floor(result['count']+.5))
    result['links'] = value.get('links') is not False
    return result


class FilePreferences:
    def __init__(self, path): self.path = Path(path)

    def load(self):
        try:
            if self.path.stat().st_size > 4096: return normalize({})
            return normalize(json.loads(self.path.read_text(encoding='utf-8')), allow_scenes=True)
        except (OSError, ValueError): return normalize({})

    def save(self, value):
        name = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(dir=self.path.parent, prefix='.appearance-')
            with os.fdopen(fd,'w',encoding='utf-8') as out:
                json.dump(normalize(value, allow_scenes=True), out)
            os.replace(name, self.path)
            return True
        except OSError: return False
        finally:
            if name:
                try: Path(name).unlink(missing_ok=True)
                except OSError: pass  # Preference failure must not break the host.

    def reset(self):
        try: self.path.unlink(missing_ok=True); return True
        except OSError: return False
