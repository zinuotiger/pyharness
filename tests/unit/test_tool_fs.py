"""tests/unit/test_tool_fs.py — 文件系统工具族 fs.* 单测(F034/F035/F036/critical 锚点)。

覆盖清单(specs/tool_fs.py.md + CONSTRAINTS-03 T-09 契约):
- resolve_in_workspace 几何第二道闸:POL-FS-1(绝对越界)/POL-FS-2(.. 逃逸)/
  POL-FS-3(junction 终解析越界)/只读例外目录(security.policy.read_extra_dirs)
- fs.read_file:写读往返、UTF-8 BOM 自动剥离、二进制(NUL)拒读 TLB-806、
  非 UTF-8 拒读、缺失 TLB-802、目录 TLB-805、>64KB 转 spill(truncated+spill_ref,
  全文不进上下文)、阈值可配、>10MB spill 上限 PERS-223、凭据纵深 POL-CRED-1、
  出口脱敏(ctx.redact)
- fs.write_file:自动建目录、临时文件+rename 原子写、失败清临时文件且原文件
  完好、append 追加、>1MB 契约层拒 TLB-803(零执行)、越界拒
- fs.list_dir:隐藏默认隐藏/all=True、glob 前缀过滤、1 层递归、>500 截断留痕
  (恰 500 不误标)、缺失/文件路径 TLB-802
- fs.delete_file:真实删除、缺失 TLB-802、目录 TLB-805、danger=critical 注册即拒
  锚点(can_use False → 管道拒,Provider 零调用,文件原样,INV-05)
- register:五要素 ToolDefinition、Provider 绑定、重名 TLB-801、schema 编译

注:guard/executor 全管道拒绝语义已在 test_tools_guard/test_tools_executor 覆盖,
本文件专注 Provider 侧纵深与工具自身契约;集成测试以最小替身走真实 executor 关3。
"""
import asyncio
import os
import subprocess
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from pyharness.config import Settings
from pyharness.core import tool_fs as fs
from pyharness.core.tools_guard import Decision, ToolCall
from pyharness.core.tools_executor import ToolExecutor
from pyharness.core.tools_registry import ToolRegistry
from pyharness.errors import PyHError, ToolError

# ===================================================================== 替身
class FakeScope:
    """会话 scope 替身:policy(workspace_root 同型)+ can_use 前置查权。"""

    def __init__(self, root: Path, *, deny: set[str] | None = None):
        self.policy = SimpleNamespace(workspace_root=str(root))
        self.deny = set() if deny is None else set(deny)
        self.asked: list[str] = []

    def can_use(self, name: str) -> bool:
        self.asked.append(name)
        return name not in self.deny


class FakeSession:
    """异步事件落点替身(真实 SessionLog 同型:append → 记录)。"""

    def __init__(self, sid: str = "s-tool-fs") -> None:
        self.sid = sid
        self.events: list[dict] = []
        self._seq = 0

    async def append(self, type_, payload, *, actor, **kw):
        self._seq += 1
        self.events.append({"type": type_, "payload": dict(payload),
                            "actor": actor, "seq": self._seq})
        return SimpleNamespace(seq=self._seq)

    def of(self, type_) -> list[dict]:
        return [e for e in self.events if e["type"] == type_]


class FakeSpill:
    """ctx.storage.spill 替身(put 记录原文/kind,返回 F039 契约 dict)。"""

    def __init__(self) -> None:
        self.put_calls: list[tuple[str, str]] = []

    async def put(self, text: str, kind: str) -> dict:
        self.put_calls.append((text, kind))
        return {"spilled": True, "ref": f"spill/{kind}/1",
                "chars": len(text), "lines": text.count("\n") + 1,
                "preview": text[:500]}


class AllowGuard:
    """guard 链替身:全 allow(专注关3 Provider 执行与工具自身契约)。"""

    async def evaluate(self, call, scope) -> Decision:
        return Decision.ALLOW


class MaskRedact:
    """ctx.redact 替身:疑似密钥打码(验证出口脱敏被工具调用)。"""

    def __call__(self, text: str) -> str:
        return text.replace("sk-SECRET123", "sk-***")


def make_ctx(root: Path, *, spill=None, cfg: Settings | None = None,
             scope=None, redact=None, session=None, guard=None) -> SimpleNamespace:
    """工具 handler 的 ctx 替身(注入面 = scope/cfg/storage/redact/session/guard)。"""
    return SimpleNamespace(
        scope=scope if scope is not None else FakeScope(root),
        cfg=cfg if cfg is not None else Settings(),
        storage=SimpleNamespace(spill=spill) if spill is not None else None,
        redact=redact,
        session=session if session is not None else FakeSession(),
        guard=guard if guard is not None else AllowGuard())


# 工具面直调:先写后读的小工具(单测内重复使用)
async def _write(ctx, path: str, content: str, **kw) -> dict:
    return await fs.write_file({"path": path, "content": content, **kw}, ctx)


async def _read(ctx, path: str, **kw) -> dict:
    return await fs.read_file({"path": path, **kw}, ctx)


# ================================================= resolve_in_workspace 几何闸
def test_resolve_absolute_inside(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    ctx = make_ctx(ws)
    target = ws / "sub" / "f.txt"
    r = fs.resolve_in_workspace(str(target), ctx)
    assert r == target.resolve()
    assert str(r).startswith(str(ws.resolve()))       # 归一后仍 workspace 内


def test_resolve_relative_anchors_workspace(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    ctx = make_ctx(ws)
    r = fs.resolve_in_workspace("sub/f.txt", ctx)
    assert r == (ws / "sub" / "f.txt").resolve()
    # 空串/"." 归一为 workspace 根本身
    assert fs.resolve_in_workspace(".", ctx) == ws.resolve()
    assert fs.resolve_in_workspace("", ctx) == ws.resolve()


def test_resolve_polfs1_absolute_outside(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    ctx = make_ctx(ws)
    with pytest.raises(PyHError) as ei:
        fs.resolve_in_workspace(str(outside), ctx)
    assert ei.value.code == "GRD-401"
    assert ei.value.ctx["reason"] == "POL-FS-1"


@pytest.mark.parametrize("raw", ["../outside.txt", "../../etc/passwd",
                                 "a/../../b.txt"])
def test_resolve_polfs2_dotdot_escape(tmp_path: Path, raw: str):
    ws = tmp_path / "ws"
    ws.mkdir()
    ctx = make_ctx(ws)
    with pytest.raises(PyHError) as ei:
        fs.resolve_in_workspace(raw, ctx)
    assert ei.value.code == "GRD-401"
    assert ei.value.ctx["reason"] == "POL-FS-2"


def test_resolve_dotdot_inside_normalizes_ok(tmp_path: Path):
    """词法含 .. 但归一后仍在 workspace 内 → 放行(与 guard g3 同判)。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "sub").mkdir()
    ctx = make_ctx(ws)
    r = fs.resolve_in_workspace("sub/../f.txt", ctx)
    assert r == (ws / "f.txt").resolve()


def _make_junction(link: Path, target: Path) -> bool:
    """Windows junction 免管理员;创建失败 → False(调用方跳过)。"""
    rc = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL).returncode
    return rc == 0


def test_resolve_polfs3_junction_escape(tmp_path: Path):
    """POL-FS-3:junction 终解析越界(词法在界内,真实在界外)→ 拒。

    读(writable=False)与写(writable=True)均拒——只读例外目录未命中时无宽限。
    """
    ws = tmp_path / "ws"
    outside = tmp_path / "outside"
    ws.mkdir()
    outside.mkdir()
    link = ws / "escape"
    if not _make_junction(link, outside):
        pytest.skip("环境不支持创建 junction(无 mklink 权限)")
    try:
        ctx = make_ctx(ws)
        for writable in (False, True):
            with pytest.raises(PyHError) as ei:
                fs.resolve_in_workspace(str(link), ctx, writable=writable)
            assert ei.value.code == "GRD-401"
            assert ei.value.ctx["reason"] == "POL-FS-3"
    finally:
        if os.path.exists(link):               # 只删联接本身,不追目标
            os.rmdir(link)


def test_resolve_extra_read_dir_exception(tmp_path: Path):
    """只读例外目录(security.policy.read_extra_dirs):junction 终解析落点 ∈ 例外
    目录 → writable=False 放行;writable=True(写/删)仍拒。"""
    ws = tmp_path / "ws"
    outside = tmp_path / "outside"
    ws.mkdir()
    outside.mkdir()
    (outside / "data.txt").write_text("extra", encoding="utf-8")
    link = ws / "data"
    if not _make_junction(link, outside):
        pytest.skip("环境不支持创建 junction(无 mklink 权限)")
    try:
        cfg = Settings()
        cfg.security.policy.read_extra_dirs = [str(outside)]
        ctx = make_ctx(ws, cfg=cfg)
        r = fs.resolve_in_workspace("data/data.txt", ctx, writable=False)
        assert r == (outside / "data.txt").resolve()
        # 写路径:只读例外不适用 → 拒
        with pytest.raises(PyHError) as ei:
            fs.resolve_in_workspace("data/data.txt", ctx, writable=True)
        assert ei.value.ctx["reason"] == "POL-FS-3"
    finally:
        if os.path.exists(link):
            os.rmdir(link)


def test_resolve_scope_unwired_fail_closed(tmp_path: Path):
    """scope 未接线/workspace 根缺失 → CYC-999(绝不回落 CWD 锚定)。"""
    ctx = SimpleNamespace(cfg=Settings())
    with pytest.raises(PyHError) as ei:
        fs.resolve_in_workspace("a.txt", ctx)
    assert ei.value.code == "CYC-999"


# ============================================================ fs.read_file
async def test_read_write_roundtrip(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    ctx = make_ctx(ws)
    text = "你好, world\n第二行\n"
    w = await _write(ctx, "hello.txt", text)
    assert w["path"] == str((ws / "hello.txt").resolve())
    assert w["bytes"] == len(text.encode("utf-8"))
    r = await _read(ctx, "hello.txt")
    assert r["content"] == text
    assert r["truncated"] is False
    assert r["bytes"] == len(text.encode("utf-8"))
    assert r["path"] == str((ws / "hello.txt").resolve())


async def test_read_bom_auto_strip(tmp_path: Path):
    """UTF-8 BOM(EF BB BF)自动剥离(utf-8-sig),不把 BOM 当内容。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    target = ws / "bom.txt"
    target.write_bytes(b"\xef\xbb\xbf" + "带BOM内容".encode("utf-8"))
    ctx = make_ctx(ws)
    r = await _read(ctx, "bom.txt")
    assert r["content"] == "带BOM内容"
    assert not r["content"].startswith("\ufeff")


async def test_read_missing_tlb802(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    ctx = make_ctx(ws)
    with pytest.raises(ToolError) as ei:
        await _read(ctx, "no-such.txt")
    assert ei.value.code == "TLB-802"


async def test_read_directory_tlb805(tmp_path: Path):
    ws = tmp_path / "ws"
    (ws / "adir").mkdir(parents=True)
    ctx = make_ctx(ws)
    with pytest.raises(ToolError) as ei:
        await _read(ctx, "adir")
    assert ei.value.code == "TLB-805"


async def test_read_binary_nul_tlb806(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "bin.dat").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x01\x02")
    ctx = make_ctx(ws)
    with pytest.raises(ToolError) as ei:
        await _read(ctx, "bin.dat")
    assert ei.value.code == "TLB-806"


async def test_read_non_utf8_tlb806(tmp_path: Path):
    """样本无 NUL 但非 UTF-8(GBK 字节)→ 解码失败拒读(偏离 4 归并 TLB-806)。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "gbk.txt").write_bytes("中文内容".encode("gbk"))
    ctx = make_ctx(ws)
    with pytest.raises(ToolError) as ei:
        await _read(ctx, "gbk.txt")
    assert ei.value.code == "TLB-806"


async def test_read_oversize_spills_not_fulltext(tmp_path: Path):
    """>64KB(默认阈值):只返回摘要+spill_ref,全文进 spill 不进上下文(T-09)。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    ctx = make_ctx(ws, spill=FakeSpill())
    big = "x" * 70_000
    (ws / "big.txt").write_text(big, encoding="utf-8")
    r = await _read(ctx, "big.txt")
    assert r["truncated"] is True
    assert r["bytes"] == 70_000
    assert r["content"] == big[:500]              # content 只带摘要(preview)
    assert r["spill_ref"]["ref"].startswith("spill/read/")
    assert r["spill_ref"]["chars"] == 70_000
    assert "x" * 70_000 not in str(r)             # 全文绝不进结果(上下文有界)


async def test_read_at_threshold_no_spill(tmp_path: Path):
    """恰 64KB(默认阈值)→ 全量返回不转 spill。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    ctx = make_ctx(ws)
    text = "x" * 65_536
    (ws / "edge.txt").write_text(text, encoding="utf-8")
    r = await _read(ctx, "edge.txt")
    assert r["truncated"] is False
    assert r["content"] == text


async def test_read_spill_threshold_configurable(tmp_path: Path):
    """loop.content.file_spill_bytes 可配(1024-1048576):配小阈值即触发 spill。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    cfg = Settings()
    cfg.loop.content.file_spill_bytes = 1024
    spill = FakeSpill()
    ctx = make_ctx(ws, cfg=cfg, spill=spill)
    text = "y" * 2000
    (ws / "cfg.txt").write_text(text, encoding="utf-8")
    r = await _read(ctx, "cfg.txt")
    assert r["truncated"] is True
    assert spill.put_calls == [(text, "read")]    # 原文(脱敏后)进 spill


async def test_read_over_spill_max_pers223(tmp_path: Path):
    """>spill 单文件上限(默认 10MB)→ 预检 PERS-223 拒读,指引换 F052。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    cfg = Settings()
    cfg.storage.spill.max_per_file_bytes = 4096   # 配小上限避免 10MB 测试文件
    ctx = make_ctx(ws, cfg=cfg)
    (ws / "huge.txt").write_text("z" * 5000, encoding="utf-8")
    with pytest.raises(PyHError) as ei:
        await _read(ctx, "huge.txt")
    assert ei.value.code == "PERS-223"


async def test_read_credential_polcred1(tmp_path: Path):
    """凭据文件纵深(g4 同码):清单精确路径与形态名(含 workspace 内副本)都拒。"""
    ws = tmp_path / "ws"
    (ws / "sub").mkdir(parents=True)
    (ws / "credentials.yaml").write_text("api_key: x", encoding="utf-8")
    (ws / "sub" / "credentials.yml").write_text("k: v", encoding="utf-8")
    ctx = make_ctx(ws)
    for path in ("credentials.yaml", "sub/credentials.yml"):
        with pytest.raises(PyHError) as ei:
            await _read(ctx, path)
        assert ei.value.code == "GRD-401"
        assert ei.value.ctx["reason"] == "POL-CRED-1"
        assert (ws / path).exists()               # 零副作用:文件原样


async def test_read_redact_applied(tmp_path: Path):
    """出口脱敏:ctx.redact 对全量返回与 spill 落盘文本都生效(INV-09)。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    spill = FakeSpill()
    ctx = make_ctx(ws, spill=spill, redact=MaskRedact())
    text = "key=sk-SECRET123 tail"
    (ws / "s.txt").write_text(text, encoding="utf-8")
    r = await _read(ctx, "s.txt")
    assert r["content"] == "key=sk-*** tail"      # 上下文只见掩码
    # 超限路径:落盘文本同样已脱敏
    big = "sk-SECRET123" + "x" * 70_000
    (ws / "big.txt").write_text(big, encoding="utf-8")
    await _read(ctx, "big.txt")
    assert "sk-SECRET123" not in spill.put_calls[0][0]
    assert spill.put_calls[0][0].startswith("sk-***")


# =========================================================== fs.write_file
async def test_write_auto_mkdir_nested(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    ctx = make_ctx(ws)
    w = await _write(ctx, "deep/nested/dir/f.txt", "nested")
    assert (ws / "deep" / "nested" / "dir" / "f.txt").read_text(
        encoding="utf-8") == "nested"
    assert w["bytes"] == len("nested".encode("utf-8"))


async def test_write_atomic_no_tmp_leftover(tmp_path: Path):
    """原子写成功:目录内无 .tmp-* 残留。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    ctx = make_ctx(ws)
    await _write(ctx, "a.txt", "one")
    await _write(ctx, "a.txt", "two")             # 覆写(工具层不二判,g7 职责)
    assert list(ws.glob("*.tmp-*")) == []
    assert (ws / "a.txt").read_text(encoding="utf-8") == "two"


async def test_write_atomic_failure_cleanup(tmp_path: Path, monkeypatch):
    """原子写失败:临时文件清除、原文件原样(崩溃不留半文件/半覆盖)。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "keep.txt").write_text("OLD", encoding="utf-8")
    ctx = make_ctx(ws)

    def _boom(src, dst):
        raise RuntimeError("disk full")

    monkeypatch.setattr(fs.os, "replace", _boom)
    with pytest.raises(RuntimeError):
        await _write(ctx, "keep.txt", "NEW")
    assert (ws / "keep.txt").read_text(encoding="utf-8") == "OLD"
    assert list(ws.glob("*.tmp-*")) == []         # 失败清临时文件
    # 新建目标失败同样无半文件
    with pytest.raises(RuntimeError):
        await _write(ctx, "fresh.txt", "x")
    assert not (ws / "fresh.txt").exists()


async def test_write_append_semantics(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    ctx = make_ctx(ws)
    await _write(ctx, "log.txt", "line1\n")
    await _write(ctx, "log.txt", "line2\n", mode="append")
    assert (ws / "log.txt").read_text(encoding="utf-8") == "line1\nline2\n"


async def test_write_out_of_workspace_polfs(tmp_path: Path):
    """写路径越界(绝对/.. 逃逸)→ GRD-401,零文件副作用。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    ctx = make_ctx(ws)
    outside = tmp_path / "evil.txt"
    for bad in (str(outside), "../evil.txt"):
        with pytest.raises(PyHError) as ei:
            await _write(ctx, bad, "x")
        assert ei.value.code == "GRD-401"
        assert ei.value.ctx["reason"].startswith("POL-FS-")
    assert not outside.exists()


async def test_write_over_1mb_contract_tlb803(tmp_path: Path):
    """content >1MB:schema maxLength 契约层拒(TLB-803),零执行(INV-06)。"""
    reg = ToolRegistry()
    fs.register(reg)
    with pytest.raises(ToolError) as ei:
        reg.validate_args("fs.write_file",
                          {"path": "big.txt", "content": "x" * 1_048_577})
    assert ei.value.code == "TLB-803"
    assert not (tmp_path / "big.txt").exists()
    # 恰 1MB 放行(契约边界)
    ok = reg.validate_args("fs.write_file",
                           {"path": "big.txt", "content": "x" * 1_048_576})
    assert ok["path"] == "big.txt"


# ============================================================ fs.list_dir
def _make_tree(ws: Path) -> None:
    ws.mkdir(exist_ok=True)
    (ws / "a.txt").write_text("a", encoding="utf-8")
    (ws / "b.txt").write_text("b", encoding="utf-8")
    (ws / "alpha.dat").write_text("d", encoding="utf-8")
    (ws / ".hidden").write_text("h", encoding="utf-8")
    (ws / ".git").mkdir()
    (ws / "sub").mkdir()
    (ws / "sub" / "c.log").write_text("c", encoding="utf-8")
    (ws / "sub" / ".h2").write_text("x", encoding="utf-8")


async def test_list_default_hides_dotfiles(tmp_path: Path):
    ws = tmp_path / "ws"
    _make_tree(ws)
    ctx = make_ctx(ws)
    r = await fs.list_dir({}, ctx)
    names = [e["name"] for e in r["entries"]]
    assert ".hidden" not in names and ".git" not in names
    assert names == sorted(names)                 # 稳定排序
    assert {"a.txt", "b.txt", "alpha.dat", "sub"} <= set(names)
    assert r["truncated"] is False


async def test_list_entry_fields(tmp_path: Path):
    ws = tmp_path / "ws"
    _make_tree(ws)
    ctx = make_ctx(ws)
    r = await fs.list_dir({}, ctx)
    by = {e["name"]: e for e in r["entries"]}
    a = by["a.txt"]
    assert a["type"] == "file" and a["size"] == 1
    datetime.fromisoformat(a["modified"])         # ISO 8601 可解析
    assert by["sub"]["type"] == "dir"
    assert by["sub"]["size"] is None              # size 仅文件


async def test_list_all_shows_hidden(tmp_path: Path):
    ws = tmp_path / "ws"
    _make_tree(ws)
    ctx = make_ctx(ws)
    r = await fs.list_dir({"all": True}, ctx)
    names = {e["name"] for e in r["entries"]}
    assert ".hidden" in names and ".git" in names


async def test_list_glob_prefix_filter(tmp_path: Path):
    """glob 为名称前缀匹配(伪码 startswith 权威,偏离 8)。"""
    ws = tmp_path / "ws"
    _make_tree(ws)
    ctx = make_ctx(ws)
    r = await fs.list_dir({"glob": "alpha"}, ctx)
    assert [e["name"] for e in r["entries"]] == ["alpha.dat"]
    r2 = await fs.list_dir({"path": "sub", "glob": "c."}, ctx)
    assert [e["name"] for e in r2["entries"]] == ["c.log"]


async def test_list_recursive_one_level(tmp_path: Path):
    """recursive=1 层递归:含直接子目录条目(description 同谓词,偏离 7)。"""
    ws = tmp_path / "ws"
    _make_tree(ws)
    ctx = make_ctx(ws)
    r = await fs.list_dir({"recursive": True}, ctx)
    names = {e["name"] for e in r["entries"]}
    assert "c.log" in names                        # 子目录条目进入
    assert "a.txt" in names and "sub" in names
    assert not names & {".hidden", ".h2", ".git"}  # 隐藏仍默认隐藏


async def test_list_missing_or_file_tlb802(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "plain.txt").write_text("x", encoding="utf-8")
    ctx = make_ctx(ws)
    for path in ("no-dir", "plain.txt"):
        with pytest.raises(ToolError) as ei:
            await fs.list_dir({"path": path}, ctx)
        assert ei.value.code == "TLB-802"


async def test_list_truncation_over_500(tmp_path: Path):
    """>500 条目:截断留痕(entries=500,truncated=True),巨目录不爆上下文。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    for i in range(505):
        (ws / f"f{i:03d}").write_text("", encoding="utf-8")
    ctx = make_ctx(ws)
    r = await fs.list_dir({}, ctx)
    assert len(r["entries"]) == 500
    assert r["truncated"] is True
    assert [e["name"] for e in r["entries"]] == \
        [f"f{i:03d}" for i in range(500)]          # 确定性截断(排序后前 500)


async def test_list_exactly_500_no_false_truncated(tmp_path: Path):
    """恰 500 条目:完整返回,不误标 truncated(>500 才截断,偏离 6)。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    for i in range(500):
        (ws / f"g{i:03d}").write_text("", encoding="utf-8")
    ctx = make_ctx(ws)
    r = await fs.list_dir({}, ctx)
    assert len(r["entries"]) == 500
    assert r["truncated"] is False


# ========================================================== fs.delete_file
async def test_delete_removes_file(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "gone.txt").write_text("x", encoding="utf-8")
    ctx = make_ctx(ws)
    r = await fs.delete_file({"path": "gone.txt"}, ctx)
    assert r == {"deleted": str((ws / "gone.txt").resolve())}
    assert not (ws / "gone.txt").exists()


async def test_delete_missing_tlb802(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    ctx = make_ctx(ws)
    with pytest.raises(ToolError) as ei:
        await fs.delete_file({"path": "nope.txt"}, ctx)
    assert ei.value.code == "TLB-802"


async def test_delete_directory_tlb805(tmp_path: Path):
    ws = tmp_path / "ws"
    (ws / "dir").mkdir(parents=True)
    ctx = make_ctx(ws)
    with pytest.raises(ToolError) as ei:
        await fs.delete_file({"path": "dir"}, ctx)
    assert ei.value.code == "TLB-805"
    assert (ws / "dir").is_dir()                  # 零副作用


async def test_delete_out_of_workspace_polfs(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    outside = tmp_path / "victim.txt"
    outside.write_text("x", encoding="utf-8")
    ctx = make_ctx(ws)
    with pytest.raises(PyHError) as ei:
        await fs.delete_file({"path": str(outside)}, ctx)
    assert ei.value.code == "GRD-401"
    assert ei.value.ctx["reason"] == "POL-FS-1"
    assert outside.exists()                       # 越界零副作用


# ============================================================== register 五要素
def test_register_five_elements_and_names():
    reg = ToolRegistry()
    names = fs.register(reg)
    assert names == list(fs.PROVIDERS) == \
        ["fs.read_file", "fs.write_file", "fs.list_dir", "fs.delete_file"]
    for n in names:                                # 五要素齐备才可注册
        d = reg.lookup(n)
        assert d.name and d.description and d.schema and d.danger and d.owner
        reg.get_model(n)                           # schema 编译通过(F026)
    assert reg.lookup("fs.read_file").danger == "low"
    assert reg.lookup("fs.write_file").danger == "low"
    assert reg.lookup("fs.list_dir").danger == "none"
    assert reg.lookup("fs.delete_file").danger == "critical"   # 注册即拒锚点
    assert reg.lookup_provider("fs.read_file") is fs.read_file  # Provider 绑定
    # schema 契约:content maxLength=1MB;required 齐备
    ws = reg.lookup("fs.write_file").schema
    assert ws["properties"]["content"]["maxLength"] == 1_048_576
    assert ws["required"] == ["path", "content"]


def test_register_duplicate_tlb801():
    reg = ToolRegistry()
    fs.register(reg)
    with pytest.raises(ToolError) as ei:
        fs.register(reg)
    assert ei.value.code == "TLB-801"


# ================================== 集成:真实 executor 关3(Provider 形态契约)
def _exec_ctx(ws: Path, scope: FakeScope, session: FakeSession) -> SimpleNamespace:
    return SimpleNamespace(
        scope=scope, cfg=Settings(),
        storage=SimpleNamespace(spill=FakeSpill()),
        redact=None,
        session=session, guard=AllowGuard())


async def test_executor_pipeline_write_then_read(tmp_path: Path):
    """tool_fs 以裸 async handler 绑定 Provider → 真实 executor 关3 可执行。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    reg = ToolRegistry()
    fs.register(reg)                               # 定义 + Provider 一并登记
    ex = ToolExecutor(registry=reg)
    scope = FakeScope(ws)
    sess = FakeSession()
    ctx = _exec_ctx(ws, scope, sess)

    r1 = await ex.execute(
        ToolCall(name="fs.write_file", raw_args={"path": "a.txt",
                                                 "content": "hello"},
                 call_id="c1"), ctx)
    assert r1.ok and not r1.truncated
    assert (ws / "a.txt").read_text(encoding="utf-8") == "hello"

    r2 = await ex.execute(
        ToolCall(name="fs.read_file", raw_args={"path": "a.txt"},
                 call_id="c2"), ctx)
    assert r2.ok
    assert "hello" in r2.summary
    # 每调用恰一条 tool.result(审计可证)
    assert len(sess.of("tool.result")) == 2
    assert sess.of("tool.result")[0]["payload"]["name"] == "fs.write_file"


async def test_executor_critical_delete_denied_zero_side_effect(tmp_path: Path):
    """fs.delete_file 注册即拒锚点:scope 前置(critical 不可见)→ 管道拒,
    Provider 零调用、文件原样(INV-05,审计特征 guard 拒绝无 tool.result)。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "keep.txt").write_text("precious", encoding="utf-8")
    reg = ToolRegistry()
    fs.register(reg)
    ex = ToolExecutor(registry=reg)
    scope = FakeScope(ws, deny={"fs.delete_file"})  # critical → can_use False
    sess = FakeSession()
    ctx = _exec_ctx(ws, scope, sess)

    r = await ex.execute(
        ToolCall(name="fs.delete_file", raw_args={"path": "keep.txt"},
                 call_id="c-del"), ctx)
    assert r.ok is False
    assert r.summary.startswith("guard 拒绝")
    assert (ws / "keep.txt").read_text(encoding="utf-8") == "precious"
    assert len(sess.of("guard.rejected")) == 1     # 强同步拒绝留痕
    assert sess.of("tool.result") == []            # 无 result = Provider 零执行
