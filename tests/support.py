from copy import deepcopy
from pathlib import Path

from pyharness.config import DEFAULTS, Settings


def isolated_settings(root: Path) -> Settings:
    data = deepcopy(DEFAULTS)
    data['storage'].update(root=str(root), sessions_dir=str(root/'sessions'),
                           workspaces_dir=str(root/'workspaces'),
                           spill_dir=str(root/'spill'), db_path=str(root/'index.db'))
    data['plugins'].update(dir=str(root/'plugins'), enabled=[], mcp_servers=[])
    data['skills']['dir'] = str(root/'skills')
    data['security']['credentials']['file'] = str(root/'credentials.yaml')
    data['llm'].update(base_url='http://127.0.0.1:1', api_key='env:PYHARNESS_SYNTHETIC_KEY', fallback_models=[])
    return Settings.model_validate(data)
