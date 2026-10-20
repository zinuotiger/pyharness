"""Versioned product definitions; never a second execution history."""
from __future__ import annotations

from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator

ProjectId = Annotated[str, Field(pattern=r'^p-[0-9a-f]{16}$')]
Digest = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]


class ProjectRevision(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)

    schema_version: Literal[1] = 1
    project_id: ProjectId
    revision: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default='', max_length=2000)
    archived: bool = False

    @field_validator('name')
    @classmethod
    def nonblank_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError('project name must not be blank')
        return value
