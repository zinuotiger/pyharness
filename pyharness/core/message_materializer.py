"""Narrow optional boundary: core knows messages, never application artifacts."""
from typing import Protocol, Any


class MessageMaterializer(Protocol):
    async def materialize_messages(self, messages: list[dict], ctx: Any) -> list[dict]: ...
    def validate_answer(self, content: str, ctx: Any) -> str: ...
