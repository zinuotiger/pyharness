"""Ephemeral capture capability: private bytes and final source verification only."""
from dataclasses import dataclass,field
from typing import Callable


@dataclass
class GitCaptureCandidate:
    contents: dict[str,bytes] = field(default_factory=dict)
    tracked_paths: tuple[str,...] = ()
    identity_digest: str = ''
    verify: Callable[[],None] | None = None
    observation: dict | None = None
    before_body_read: Callable[[],None] | None = None


async def collect_workspace_git(source,metadata,command,*,before_body_read=None):
    from pyharness.application.workspace_git import preview_git_layers
    candidate=GitCaptureCandidate(before_body_read=before_body_read)
    candidate.observation=await preview_git_layers(source,metadata,command,capture=candidate)
    return candidate
