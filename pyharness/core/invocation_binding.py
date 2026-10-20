"""Task-local provider identity, installed only after the executor's governance gate."""
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class InvocationBinding:
    call_id: str
    tool_call_seq: int
    tool: str
    inputs_digest: str


current_invocation: ContextVar[InvocationBinding | None] = ContextVar('provider_invocation', default=None)
