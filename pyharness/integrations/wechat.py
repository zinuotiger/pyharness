"""Personal WeChat boundary. No undocumented login or private chat extraction."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Protocol
from pydantic import BaseModel, ConfigDict, Field


@dataclass(frozen=True)
class PersonalWechatMessage:
    message_id: str
    conversation_id: str
    sender_id: str
    text: str


class PersonalWechatBridge(Protocol):
    """A selected, user-authorized provider must implement lifecycle and dedup IDs.

    Implementations must supply their documented authentication and account model;
    this protocol does not imply that an official personal messaging API exists.
    """
    async def start(self, receive: Callable[[PersonalWechatMessage], Awaitable[None]]) -> None: ...
    async def send(self, conversation_id: str, text: str, *, message_id: str) -> None: ...
    async def close(self) -> None: ...


class ManualWechatRequest(BaseModel):
    """Executable user-mediated relay, explicitly separate from automatic messaging."""
    model_config = ConfigDict(extra='forbid')
    text: str = Field(min_length=1,max_length=32000)
    session_id: str
    request_id: str = Field(pattern=r'^[A-Za-z0-9_.:-]{1,120}$')
    agent_id: str | None = None
    agent_version: int | None = Field(default=None,ge=1)

    def run_request(self):
        return {'text':self.text,'session_id':self.session_id,'request_id':'wechat-manual:'+self.request_id,
                'agent_id':self.agent_id,'agent_version':self.agent_version}
