"""F061 图片附件:魔数/大小/数量/内容寻址(2026-09-21,L-22 闭合)。

**修复前实测**:附件入站只做 schema 形状组装 —— `mime` 由客户端自报且缺省回落
`image/png`、无魔数校验、无大小/数量上限、`sha256` 可省略回落**全零占位**
(⇒ 同内容不去重、伪造哈希被接受)。PRD-Core F061 的验收伪代码要求:
`validate_image`(魔数,不信扩展名)→ 单张 ≤10MB → 单消息 ≤5 张 → 内容寻址。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from pyharness.application import ApplicationService
from pyharness.bus import EventBus
from pyharness.config import DEFAULTS, Settings
from pyharness.core import attachment as att
from pyharness.errors import PyHError

# ---------------------------------------------------------------- 字节夹具
PNG_1x2 = (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR"
           + (1).to_bytes(4, "big") + (2).to_bytes(4, "big") + b"\x08\x02")
GIF_3x4 = b"GIF89a" + (3).to_bytes(2, "little") + (4).to_bytes(2, "little") + b"\x00" * 4
JPEG_5x6 = (b"\xff\xd8\xff\xc0" + b"\x00\x11" + b"\x08"
            + (6).to_bytes(2, "big") + (5).to_bytes(2, "big") + b"\x00" * 8)
WEBP_7x8 = (b"RIFF" + (30).to_bytes(4, "little") + b"WEBP" + b"VP8X"
            + b"\x00" * 8 + (7 - 1).to_bytes(3, "little")
            + (8 - 1).to_bytes(3, "little") + b"\x00")
NOT_IMAGE = b"MZ\x90\x00" + b"\x00" * 64              # PE 头:伪装成 .png 的可执行


def test_detect_by_magic_not_extension():
    """**不信扩展名/自报 mime**:按魔数定类型。"""
    assert att.detect_image(PNG_1x2) == ("image/png", ".png")
    assert att.detect_image(GIF_3x4) == ("image/gif", ".gif")
    assert att.detect_image(JPEG_5x6) == ("image/jpeg", ".jpg")
    assert att.detect_image(WEBP_7x8) == ("image/webp", ".webp")
    assert att.detect_image(NOT_IMAGE) is None


def test_validate_image_reads_real_dimensions_and_hash():
    """尺寸来自**文件内容**(四格式各一),`sha256` 为真实内容哈希(非全零占位)。"""
    import hashlib
    for raw, mime, dims in ((PNG_1x2, "image/png", (1, 2)),
                            (GIF_3x4, "image/gif", (3, 4)),
                            (JPEG_5x6, "image/jpeg", (5, 6)),
                            (WEBP_7x8, "image/webp", (7, 8))):
        info = att.validate_image(raw)
        assert info["mime"] == mime and (info["w"], info["h"]) == dims, info
        assert info["sha256"] == hashlib.sha256(raw).hexdigest()
        assert info["size_bytes"] == len(raw)


@pytest.mark.parametrize("raw,reason", [
    (NOT_IMAGE, "magic-mismatch"),
    (b"", "empty"),
    (b"\x89PNG\r\n\x1a\n" + b"\x00" * 100, "dims-unreadable"),   # 魔数对但头截断
])
def test_validate_image_rejects(raw, reason):
    """非图片/空/头截断 → ATT-001(零写盘),错误体带原因。"""
    with pytest.raises(PyHError) as ei:
        att.validate_image(raw)
    assert ei.value.code == "ATT-001"
    assert ei.value.ctx.get("reason") == reason


def test_validate_image_enforces_size_and_whitelist():
    """大小上限与 mime 白名单(均可由配置注入)。"""
    with pytest.raises(PyHError) as ei:
        att.validate_image(PNG_1x2, max_bytes=len(PNG_1x2) - 1)
    assert ei.value.ctx.get("reason") == "too-big"
    with pytest.raises(PyHError) as ei:
        att.validate_image(PNG_1x2, mime_whitelist=("image/jpeg",))
    assert ei.value.ctx.get("reason") == "mime-not-allowed"


def test_store_content_addressed_dedup(tmp_path):
    """内容寻址:同内容 → 同路径(去重,不重写);路径在给定工作区内。"""
    info = att.validate_image(PNG_1x2)
    p1 = att.store_content_addressed(PNG_1x2, info, workspace=tmp_path)
    stamp = __import__("os").stat(p1).st_mtime_ns
    p2 = att.store_content_addressed(PNG_1x2, info, workspace=tmp_path)
    assert p1 == p2 and p1.endswith(f"{info['sha256'][:16]}.png")
    assert str(tmp_path) in p1
    assert __import__("os").stat(p2).st_mtime_ns == stamp   # 已存在 ⇒ 不重写
    # 不同内容 → 不同路径
    other = att.validate_image(GIF_3x4)
    assert att.store_content_addressed(GIF_3x4, other, workspace=tmp_path) != p1


def test_store_without_workspace_fails_closed():
    """工作区缺位 → ATT-001(拒绝落在任意宿主路径)。"""
    info = att.validate_image(PNG_1x2)
    with pytest.raises(PyHError) as ei:
        att.store_content_addressed(PNG_1x2, info, workspace="")
    assert ei.value.ctx.get("reason") == "no-workspace"


# ---------------------------------------------------------------- 服务层装配
def _service(tmp_path, **att_cfg) -> ApplicationService:
    raw = Settings.model_validate(DEFAULTS).model_dump()
    raw["storage"]["root"] = str(tmp_path)
    raw["storage"]["workspaces_dir"] = str(tmp_path / "workspaces")
    raw.setdefault("security", {}).setdefault("attachment", {}).update(att_cfg)
    ctx = SimpleNamespace(bus=EventBus(), settings=Settings.model_validate(raw))
    return ApplicationService(ctx, channel="desktop", tenant_id="default")


def test_service_ingests_attachment_into_session_workspace(tmp_path):
    """端到端:载荷取真实 sha256/尺寸,`file_path` 落在**会话工作区内**。"""
    svc = _service(tmp_path)
    src = tmp_path / "pic.png"
    src.write_bytes(PNG_1x2)
    payloads = svc.attachment_payloads([{"file_path": str(src)}], sid="s-att-1")
    assert len(payloads) == 1
    p = payloads[0]
    import hashlib
    assert p["sha256"] == hashlib.sha256(PNG_1x2).hexdigest() != "0" * 64
    assert (p["w"], p["h"]) == (1, 2) and p["size_bytes"] == len(PNG_1x2)
    assert p["mime"] == "image/png"
    assert str(tmp_path / "workspaces" / "s-att-1") in p["file_path"]
    assert p["file_path"].endswith(".png")


def test_service_rejects_disguised_file_and_ignores_self_reported_mime(tmp_path):
    """伪装文件(扩展名 .png 实为 PE)→ ATT-001;自报 mime 不参与判定。"""
    svc = _service(tmp_path)
    evil = tmp_path / "evil.png"
    evil.write_bytes(NOT_IMAGE)
    with pytest.raises(PyHError) as ei:
        svc.attachment_payloads([{"file_path": str(evil), "mime": "image/png"}],
                                sid="s-att-2")
    assert ei.value.code == "ATT-001" and ei.value.ctx.get("reason") == "magic-mismatch"

    # 自报 mime 谎报为 jpeg,实际 GIF → 以魔数为准
    real = tmp_path / "a.gif"
    real.write_bytes(GIF_3x4)
    out = svc.attachment_payloads([{"file_path": str(real), "mime": "image/jpeg"}],
                                  sid="s-att-3")
    assert out[0]["mime"] == "image/gif"
    assert not (tmp_path / "workspaces" / "s-att-2" / "attachments").exists(), \
        "被拒的附件零写盘"


def test_service_enforces_count_limit_from_config(tmp_path):
    """数量上限取自 ``security.attachment.max_per_message``(**该键此前是死配置**)。"""
    svc = _service(tmp_path, max_per_message=1)
    a = tmp_path / "a.png"
    b = tmp_path / "b.png"
    a.write_bytes(PNG_1x2)
    b.write_bytes(GIF_3x4)
    refs = [{"file_path": str(a)}, {"file_path": str(b)}]
    with pytest.raises(PyHError) as ei:
        svc.attachment_payloads(refs, sid="s-att-4")
    assert ei.value.code == "ATT-001" and ei.value.ctx.get("reason") == "too-many"
    # 上限内可用
    assert len(svc.attachment_payloads([{"file_path": str(a)}], sid="s-att-4")) == 1


def test_service_enforces_size_limit_from_config(tmp_path):
    """大小上限取自 ``security.attachment.max_bytes``(同属此前死配置)。"""
    svc = _service(tmp_path, max_bytes=len(PNG_1x2) - 1)
    a = tmp_path / "a.png"
    a.write_bytes(PNG_1x2)
    with pytest.raises(PyHError) as ei:
        svc.attachment_payloads([{"file_path": str(a)}], sid="s-att-5")
    assert ei.value.ctx.get("reason") == "too-big"


def test_service_dedups_same_content_across_calls(tmp_path):
    """同内容两次入站 → 同一落盘路径(内容寻址去重)。"""
    svc = _service(tmp_path)
    a = tmp_path / "one.png"
    b = tmp_path / "two.png"
    a.write_bytes(PNG_1x2)
    b.write_bytes(PNG_1x2)
    p1 = svc.attachment_payloads([{"file_path": str(a)}], sid="s-att-6")[0]
    p2 = svc.attachment_payloads([{"file_path": str(b)}], sid="s-att-6")[0]
    assert p1["file_path"] == p2["file_path"]
    assert len(list((tmp_path / "workspaces" / "s-att-6" / "attachments")
                    .iterdir())) == 1


def test_service_missing_file_is_rejected_not_silently_skipped(tmp_path):
    """路径不可读 → ATT-001(不静默跳过:调用方必须知道附件没进来)。"""
    svc = _service(tmp_path)
    with pytest.raises(PyHError) as ei:
        svc.attachment_payloads([{"file_path": str(tmp_path / "nope.png")}],
                                sid="s-att-7")
    assert ei.value.code == "ATT-001" and ei.value.ctx.get("reason") == "unreadable"


def test_png_parser_matches_independent_struct_read_on_real_asset():
    """**真实大文件**交叉验证:解析器结果 == 独立 ``struct`` 读 IHDR 的结果。

    用仓库自带 PNG(数百 KB)而非 26 字节夹具,防止"只在玩具字节上成立"。
    资产若被重新生成,断言仍成立(对同字节做两路读取的差分,不硬编码尺寸)。
    """
    import struct
    from pathlib import Path
    png = Path(__file__).resolve().parents[2] / "docs" / "architecture.png"
    if not png.exists():                       # 资产缺失:显式跳过而非静默通过
        pytest.skip("docs/architecture.png 不存在")
    raw = png.read_bytes()
    info = att.validate_image(raw)
    assert info["mime"] == "image/png"
    assert (info["w"], info["h"]) == struct.unpack(">II", raw[16:24])
    assert info["size_bytes"] == len(raw) and png.stat().st_size == len(raw)
