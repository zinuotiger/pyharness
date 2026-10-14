"""Payload session: complete migrated implementation."""
from __future__ import annotations
from typing import Optional
from pydantic import Field

from pyharness.events.payload_base import _PayloadBase

class SessionCreatedPayload(_PayloadBase):
    """session.created:首事件恒 seq=1;title 可为空串(F042 随后自动命名)。"""
    title: str
    model: str = Field(min_length=1)

class SessionRenamedPayload(_PayloadBase):
    """session.renamed:新标题 ≤64 字且非空;by=auto(user 显式)/auto(自动命名)。"""
    new_title: str = Field(min_length=1, max_length=64)
    by: Optional[str] = None          # "auto" | "user"(来源侧语义,不收紧)

class SessionFinishedPayload(_PayloadBase):
    """session.finished:终态只写一次(EVT-104 由状态机侧把关),reason 五枚举。"""
    reason: str                       # idle/timeout/budget/error/cancelled 由会话层语义约束

class SessionRecoveredPayload(_PayloadBase):
    """session.recovered:fixed 非空(修复动作清单),lost=被截断丢弃 seq 区间。"""
    fixed: list[str] = Field(min_length=1)
    backup: Optional[str] = None
    lost: Optional[list[int]] = None

class UserMessagePayload(_PayloadBase):
    """user.message:用户原始输入原样保存,content 非空(强同步)。"""
    content: str = Field(min_length=1)
    meta: Optional[dict] = None       # cli/web/acp 等来源通道信息

class UserMessageEditedPayload(_PayloadBase):
    """user.message_edited:只追加修正,target_seq 指向被改 user.message。"""
    target_seq: int = Field(ge=1)
    new_content: str = Field(min_length=1)

class UserFeedbackPayload(_PayloadBase):
    """user.feedback:对 agent.message 的评价,kind 三枚举,note ≤500 字。"""
    target_seq: int = Field(ge=1)
    kind: str = Field(pattern=r"^(up|down|flag)$")
    note: Optional[str] = Field(default=None, max_length=500)

class UserAttachmentImagePayload(_PayloadBase):
    """user.attachment.image:内容寻址附件;sha256=64 hex,mime 白名单四值。"""
    file_path: str = Field(min_length=1)
    mime: str = Field(pattern=r"^image/(jpeg|png|webp|gif)$")
    sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    w: int = Field(ge=1)
    h: int = Field(ge=1)
    size_bytes: Optional[int] = Field(default=None, ge=0)

class UserCommandPayload(_PayloadBase):
    """user.command:斜杠命令命中即写本事件(不产生 user.message),args 可空串。"""
    name: str = Field(min_length=1)
    args: Optional[str] = ""

# Preserve historical import and pickle identities through the compatibility facade.
SessionCreatedPayload.__module__ = 'pyharness.events.payload'
SessionRenamedPayload.__module__ = 'pyharness.events.payload'
SessionFinishedPayload.__module__ = 'pyharness.events.payload'
SessionRecoveredPayload.__module__ = 'pyharness.events.payload'
UserMessagePayload.__module__ = 'pyharness.events.payload'
UserMessageEditedPayload.__module__ = 'pyharness.events.payload'
UserFeedbackPayload.__module__ = 'pyharness.events.payload'
UserAttachmentImagePayload.__module__ = 'pyharness.events.payload'
UserCommandPayload.__module__ = 'pyharness.events.payload'
