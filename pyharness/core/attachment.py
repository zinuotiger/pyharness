"""pyharness/core/attachment.py — 图片附件校验与内容寻址落盘(F061 唯一实现)。

**为什么单独成模块**:F061 的校验面(magic bytes / 大小 / 数量 / 内容寻址)此前
**只到事件 schema 层**(mime 白名单由 payload 模型强制,但 mime 由客户端自报、
缺省回落 `image/png`,且无魔数/大小/数量校验、`sha256` 可省略回落全零占位)。
规格(PRD-Core §F061 验收伪代码)要求:魔数校验("不信扩展名")→ 单张 ≤10MB →
单消息 ≤5 张 → `sha256(raw)[:16]` 内容寻址去重。本模块是该规格的**单点实现**,
装配层(ApplicationService)与外壳一律经此,不得各自实现(同 core/channel.py 先例)。

安全口径:入参 `path` 来自**客户端自报**,但读到的字节一律按**不可信内容**处理
——先按魔数定类型(不看扩展名)、再按配置上限定大小,最后内容寻址落盘到会话工作区;
任一环节不满足即 `ATT-001` 拒绝(ERR.md §2.11 功能特性码,零写盘)。
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any, Optional

from pyharness.errors import PyHError

# 默认上限(CFG `security.attachment.*` 的 L1 锚点;装配层应注入配置值)
DEFAULT_MAX_BYTES = 10_485_760          # 单张 ≤10MB(PRD F061 边界条件)
DEFAULT_MAX_PER_MESSAGE = 5             # 单消息 ≤5 张(同上)
DEFAULT_MIME_WHITELIST = ("image/jpeg", "image/png", "image/webp", "image/gif")

# 魔数 → (mime, 扩展名);顺序即判定序(前缀互斥)
_MAGIC: tuple[tuple[bytes, str, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
    (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
    (b"GIF87a", "image/gif", ".gif"),
    (b"GIF89a", "image/gif", ".gif"),
)


def _att_reject(reason: str, **ctx: Any) -> None:
    """统一拒绝出口:ATT-001(功能特性码,errors.py 未登记 → 直接构造,同 QUE-001 先例)。"""
    raise PyHError("ATT-001", ctx={"reason": reason, **ctx})


def reject_attachment(reason: str, **ctx: Any) -> None:
    """对外统一拒绝出口(ATT-001);供装配层做数量/上下文类校验时复用。"""
    _att_reject(reason, **ctx)


def detect_image(raw: bytes) -> Optional[tuple[str, str]]:
    """按**魔数**判类型(不看扩展名);未命中 → None。"""
    for magic, mime, ext in _MAGIC:
        if raw.startswith(magic):
            return mime, ext
    if len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp", ".webp"
    return None


def _dims_png(raw: bytes) -> Optional[tuple[int, int]]:
    if len(raw) < 24 or raw[12:16] != b"IHDR":
        return None
    w = int.from_bytes(raw[16:20], "big")
    h = int.from_bytes(raw[20:24], "big")
    return (w, h) if w > 0 and h > 0 else None


def _dims_gif(raw: bytes) -> Optional[tuple[int, int]]:
    if len(raw) < 10:
        return None
    w = int.from_bytes(raw[6:8], "little")
    h = int.from_bytes(raw[8:10], "little")
    return (w, h) if w > 0 and h > 0 else None


def _dims_jpeg(raw: bytes) -> Optional[tuple[int, int]]:
    """扫 SOFn 标记取尺寸(SOF0/1/2/3/5/6/7/9/10/11/13/14/15)。"""
    i = 2
    n = len(raw)
    while i + 9 < n:
        if raw[i] != 0xFF:
            i += 1
            continue
        marker = raw[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        seg_len = int.from_bytes(raw[i + 2:i + 4], "big")
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                      0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            h = int.from_bytes(raw[i + 5:i + 7], "big")
            w = int.from_bytes(raw[i + 7:i + 9], "big")
            return (w, h) if w > 0 and h > 0 else None
        if seg_len <= 0:
            return None
        i += 2 + seg_len
    return None


def _dims_webp(raw: bytes) -> Optional[tuple[int, int]]:
    if len(raw) < 30:
        return None
    fourcc = raw[12:16]
    if fourcc == b"VP8X":
        w = int.from_bytes(raw[24:27], "little") + 1
        h = int.from_bytes(raw[27:30], "little") + 1
        return (w, h) if w > 0 and h > 0 else None
    if fourcc == b"VP8L" and len(raw) >= 25 and raw[20] == 0x2F:
        bits = int.from_bytes(raw[21:25], "little")
        w = (bits & 0x3FFF) + 1
        h = ((bits >> 14) & 0x3FFF) + 1
        return (w, h) if w > 0 and h > 0 else None
    if fourcc == b"VP8 " and len(raw) >= 30:
        w = int.from_bytes(raw[26:28], "little") & 0x3FFF
        h = int.from_bytes(raw[28:30], "little") & 0x3FFF
        return (w, h) if w > 0 and h > 0 else None
    return None


_DIMS = {"image/png": _dims_png, "image/gif": _dims_gif,
         "image/jpeg": _dims_jpeg, "image/webp": _dims_webp}


def validate_image(raw: bytes, *,
                   mime_whitelist: Any = None,
                   max_bytes: Optional[int] = None) -> dict:
    """**魔数校验 + 尺寸解析 + 大小/白名单**校验(不信扩展名/自报 mime)。

    返回 ``{"mime","ext","w","h","size_bytes","sha256"}``;任一不满足 → ``ATT-001``。
    """
    limit = int(max_bytes or DEFAULT_MAX_BYTES)
    if not raw:
        _att_reject("empty", size_bytes=0)
    if len(raw) > limit:
        _att_reject("too-big", size_bytes=len(raw), limit=limit)
    hit = detect_image(raw)
    if hit is None:
        _att_reject("magic-mismatch", size_bytes=len(raw),
                    advice="仅接受 jpeg/png/webp/gif(按魔数判定,不看扩展名)")
    mime, ext = hit
    allow = tuple(mime_whitelist or DEFAULT_MIME_WHITELIST)
    if mime not in allow:
        _att_reject("mime-not-allowed", mime=mime, allowlist=list(allow))
    dims = _DIMS[mime](raw)
    if dims is None:
        _att_reject("dims-unreadable", mime=mime,
                    advice="无法解析图像尺寸:文件可能截断或伪装")
    w, h = dims
    return {"mime": mime, "ext": ext, "w": int(w), "h": int(h),
            "size_bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest()}


def store_content_addressed(raw: bytes, info: dict, *,
                            workspace: Any) -> str:
    """内容寻址落盘:`{workspace}/attachments/{sha256[:16]}{ext}`(已存在即去重)。

    返回落盘绝对路径(即事件 ``file_path``)。工作区缺位 → ``ATT-001``(装配错误,
    fail-closed:附件必须落在**会话可见边界内**,不接受任意宿主路径)。
    """
    if not workspace:
        _att_reject("no-workspace",
                    advice="会话工作区未装配:附件必须落在工作区内(拒绝宿主任意路径)")
    d = Path(str(workspace)).expanduser() / "attachments"
    d.mkdir(parents=True, exist_ok=True)
    target = d / f"{info['sha256'][:16]}{info['ext']}"
    if not target.exists():                    # 内容寻址 ⇒ 同内容天然去重
        tmp = target.with_name(target.name + f".tmp-{os.getpid()}")
        try:
            tmp.write_bytes(raw)
            os.replace(tmp, target)
        except OSError as exc:                 # noqa: BLE001
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            # 走**本模块统一拒绝出口**:ATT-001 是 ERR.md §2.11 功能特性码、`errors.py`
            # **故意不登记**(同 QUE-001/BUSY 先例),须**直接构造**保住字面码 ——
            # 用 `raise_code("ATT-001")` 会被静默改写为 CYC-999(R19)。
            _att_reject("store-failed", path=str(target),
                        why=f"{type(exc).__name__}")
    return str(target)


def ingest_image_path(path: Any, *, workspace: Any,
                      mime_whitelist: Any = None,
                      max_bytes: Optional[int] = None) -> dict:
    """从**客户端自报路径**读字节 → 校验 → 内容寻址落盘;返回事件载荷 dict。

    路径不可读 → ``ATT-001``(不静默跳过:调用方需知道附件没进来)。
    """
    p = Path(str(path or "")).expanduser()
    try:
        raw = p.read_bytes()
    except OSError as exc:
        _att_reject("unreadable", path=str(p), why=f"{type(exc).__name__}")
    info = validate_image(raw, mime_whitelist=mime_whitelist,
                          max_bytes=max_bytes)
    stored = store_content_addressed(raw, info, workspace=workspace)
    return {"file_path": stored, "mime": info["mime"], "sha256": info["sha256"],
            "w": info["w"], "h": info["h"], "size_bytes": info["size_bytes"]}


__all__ = ["DEFAULT_MAX_BYTES", "DEFAULT_MAX_PER_MESSAGE",
           "DEFAULT_MIME_WHITELIST", "detect_image", "validate_image",
           "store_content_addressed", "ingest_image_path",
           "reject_attachment"]
