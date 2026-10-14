"""Payload base: complete migrated implementation."""
from __future__ import annotations
from pydantic import BaseModel, ConfigDict


_EVIDENCE_REF_KINDS: tuple[str, ...] = ("seq", "decision_id", "receipt_id",
                                        "segment", "test")

class _PayloadBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

# Preserve historical import and pickle identities through the compatibility facade.
_PayloadBase.__module__ = 'pyharness.events.payload'
