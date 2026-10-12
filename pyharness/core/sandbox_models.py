"""Sandbox execution DTOs shared by runtime and application projections."""
from datetime import datetime, timezone
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
class Model(BaseModel):
    model_config = ConfigDict(extra='forbid', validate_assignment=True)
def utcnow():
    return datetime.now(timezone.utc).isoformat()

class SandboxProfile(Model):
    mode: Literal['disabled', 'host_approved', 'isolated', 'remote'] = 'disabled'
    remote_connection: str | None = None
    workspace_mode: Literal['private_copy', 'private_git_clone', 'empty'] = 'private_copy'
    network: Literal['none'] = 'none'
    image: str = Field(default='python:3.13-slim', pattern=r'^[a-zA-Z0-9][a-zA-Z0-9_./:@-]{0,199}$')
    cpus: float = Field(default=1, ge=0.1, le=4)
    memory_mb: int = Field(default=256, ge=64, le=2048)
    pids_limit: int = Field(default=64, ge=8, le=256)
    timeout: int = Field(default=60, ge=1, le=600)
    output_limit: int = Field(default=65536, ge=1024, le=1048576)


class SandboxRecord(Model):
    sandbox_id: str
    run_id: str
    backend: str
    status: str
    workspace_mode: str
    network: str = 'none'
    cpus: float
    memory_mb: int
    pids_limit: int
    created_at: str = Field(default_factory=utcnow)
    error_code: str | None = None
