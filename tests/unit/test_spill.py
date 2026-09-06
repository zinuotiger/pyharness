"""tests/unit/test_spill.py — spill 输出溢出基础设施单测(specs/spill.py.md F039)。

覆盖面(spec 函数清单逐条 + DIS-SEAM §6.1 GWT + ERR PERS-221/222/223):
- enter:会话首访惰性建目录(workspace 外)、幂等、会话隔离、权限 600
  (Windows 尽力而为跳过断言)、mkdir/chmod 失败 → PERS-221
- put:SpillMeta 字段级 {spilled,ref,chars,lines,preview(头500)}、原子落盘
  原文=文件(G1)、ref 形如 <sid>/spill-xxxx.txt 会话内相对、多次 put 文件名
  唯一、超限 → PERS-223、目录写失败 → PERS-221、出口脱敏(INV-09,ctx.redact)
- read:按行取回 [start, start+limit)(G2 前 100 行+more)、默认 0/200、全文
  roundtrip、越权零读取(PERS-222:绝对/../跨会话/非 str,同 call 无部分返回)、
  文件缺失(已清理)→ TLB-802、start/limit 越界 → TLB-803、已读量累入
  counters.spill_read_chars(防循环烧预算)
- detach:幂等摘除(摘读工具/摘定位器/摘订阅)、**文件不删**(F060 读窗口)、
  双 detach 无副作用
- purge_session:会话清理后目录消失(文件+残留 tmp)、目录不存在幂等、清理
  目标逃逸(spill 根/绝对/..)→ PERS-222 拒且文件不动、跨会话不动他人文件
- register:工具表只有读(storage.spill)无 put、schema(ref 必填/start≥0/
  limit 1-1000 默认 200)、danger=none/owner=spine/timeout 15、Provider
  handle(args, ctx) 可执行
- 生命周期:subscribe_purge 订阅 session.finished → 结束后 spill 目录消失;
  他会话 finished 不动本会话;detach 摘订阅后不再清理

存储一律 tmp_path(不碰真实 ~/.pyharness);错误码经 errors.raise_code 断言
code/零副作用。Windows 上权限 600 为尽力而为(spec 偏离 4:ACL 不由 Python
管),模式断言仅 posix 执行;符号链接逃逸用例在无权限环境自动 skip。
"""
import os
import sys
import types
from pathlib import Path

import pytest

from pyharness.core import spill
from pyharness.bus import EventBus
from pyharness.config import Settings
from pyharness.core.tools_registry import ToolRegistry
from pyharness.errors import PyHError

SID = "s-abc12345"          # 本会话
SID2 = "s-other99999"       # 他会话(隔离断言)
TOOL = "storage.spill"      # LLM 可见读工具名


# ===================================================================== 替身
class FakeCounters:
    """ctx.counters 替身:bump(key, n) 计数(与模块 _bump_counter 契约一致)。"""

    def __init__(self) -> None:
        self._v: dict[str, int] = {}

    def bump(self, key: str, delta: int = 1) -> None:
        self._v[key] = self._v.get(key, 0) + delta

    def value(self, key: str) -> int:
        return self._v.get(key, 0)


def make_ctx(tmp_path: Path, *, sid: str = SID,
             max_per_file: int = spill.DEFAULT_MAX_PER_FILE,
             redact=None, counters=None, tools=None, storage=None,
             bus=None, config: Settings | None = None,
             ) -> types.SimpleNamespace:
    """spill 模块的 ctx 门面替身(config 用真 Settings + tmp 根,不碰真实目录)。"""
    if config is None:
        root = tmp_path / "spill_root"
        config = Settings(storage={"spill_dir": str(root),
                                   "spill": {"max_per_file_bytes": max_per_file}})
    sess = types.SimpleNamespace(session_id=sid, sid=sid)
    return types.SimpleNamespace(
        config=config, cfg=None, session=sess, session_id=sid,
        redact=redact, counters=counters, tools=tools, storage=storage,
        bus=bus, log=None)


def cfg_root(config: Settings) -> Path:
    """tmp 配置里的 spill 根(断言落盘位置用)。"""
    return Path(config.storage.spill_dir)


def spill_dir(config: Settings, sid: str = SID) -> Path:
    """会话私有区目录。"""
    return cfg_root(config) / sid


def files_in(config: Settings, sid: str = SID) -> list[Path]:
    """会话区现存 spill 文件(断言清理/保留用)。"""
    d = spill_dir(config, sid)
    return sorted(d.glob("spill-*.txt")) if d.exists() else []


def make_long_text(n_lines: int = 250, prefix: str = "L") -> str:
    """多行测试文本(无尾随换行;行号带零填充便于断言)。"""
    return "\n".join(f"{prefix}{i:04d}" for i in range(n_lines))


def masker(text: str) -> str:
    """FakeCtx.redact:把 sk- 开头的疑似密钥打成固定掩码(INV-09 语义)。"""
    return text.replace("sk-abcdefgh1234567890", "sk-***")


# ===================================================================== enter
class TestEnter:
    async def test_lazy_create_per_session(self, tmp_path: Path):
        """F039:会话首访惰性建目录,workspace 外、按会话隔离。"""
        ctx = make_ctx(tmp_path)
        assert not cfg_root(ctx.config).exists()          # 首访前无目录
        d = await spill.enter(ctx)
        assert d == spill_dir(ctx.config).resolve()
        assert d.is_dir()                                  # 惰性创建成功
        assert d.parent == cfg_root(ctx.config).resolve()  # 根 = spill_dir
        # 会话隔离:第二个会话目录互不干扰
        ctx2 = make_ctx(tmp_path, sid=SID2)
        d2 = await spill.enter(ctx2)
        assert d2.parent == d.parent and d2.name == SID2 and d2 != d

    async def test_idempotent(self, tmp_path: Path):
        """已存在则幂等返回同一归一目录。"""
        ctx = make_ctx(tmp_path)
        d1 = await spill.enter(ctx)
        d2 = await spill.enter(ctx)
        assert d1 == d2 and d1.is_dir()

    @pytest.mark.skipif(sys.platform == "win32", reason="Windows ACL 尽力而为(spec)")
    async def test_mode_600(self, tmp_path: Path):
        """私有区目录权限 600。"""
        ctx = make_ctx(tmp_path)
        d = await spill.enter(ctx)
        assert (d.stat().st_mode & 0o777) == 0o600

    async def test_mkdir_fail_raises_pers221(self, tmp_path: Path):
        """创建失败 → PERS-221 结构化上抛(查权限与空间)。"""
        blocker = tmp_path / "blocker"
        blocker.write_text("file", encoding="utf-8")      # 同名文件占位 → mkdir 失败
        ctx = make_ctx(tmp_path)
        ctx.config = Settings(storage={"spill_dir": str(blocker)})
        with pytest.raises(PyHError) as ei:
            await spill.enter(ctx)
        assert ei.value.code == "PERS-221"
        assert "spill" in str(ei.value.ctx.get("dir", "")).lower() or ei.value.ctx

    async def test_chmod_fail_raises_pers221(self, tmp_path: Path, monkeypatch):
        """chmod 失败同样 PERS-221(spec 伪码同一 catch)。"""
        def _boom(*_a, **_k):
            raise OSError("chmod denied")
        monkeypatch.setattr(spill.os, "chmod", _boom)
        ctx = make_ctx(tmp_path)
        with pytest.raises(PyHError) as ei:
            await spill.enter(ctx)
        assert ei.value.code == "PERS-221"

    async def test_missing_cfg_raises_pers221(self, tmp_path: Path):
        """配置缺失(storage.spill_dir 不可解析)→ PERS-221。"""
        ctx = make_ctx(tmp_path)
        ctx.config = None
        with pytest.raises(PyHError) as ei:
            await spill.enter(ctx)
        assert ei.value.code == "PERS-221"


# ===================================================================== put
class TestPut:
    async def test_meta_fields_and_file_roundtrip(self, tmp_path: Path):
        """SpillMeta 字段级 + 文件=原文原子落盘(G1:截断不吞事实)。"""
        ctx = make_ctx(tmp_path)
        text = make_long_text(5, prefix="行")
        meta = await spill.put(text, kind="fs.read_file", ctx=ctx)
        assert meta["spilled"] is True
        # ref 形如 <sid>/spill-<8hex>.txt(会话内相对,事件可 grep)
        assert meta["ref"].startswith(f"{SID}/spill-")
        assert len(meta["ref"].rsplit("/", 1)[1]) == len("spill-") + 8 + len(".txt")
        assert meta["chars"] == len(text)
        assert meta["lines"] == text.count("\n") + 1
        assert meta["preview"] == text[:spill.SUMMARY_PREVIEW]
        # 落盘文件 = 原文(唯一 spill 文件)
        files = files_in(ctx.config)
        assert len(files) == 1
        assert files[0].read_text(encoding="utf-8", newline="") == text
        # kind 为来源标注(本实现入文件名暂未用,spec 伪码文件名不含 kind)
        assert meta["ref"].endswith(".txt")

    async def test_preview_head_500_fixed(self, tmp_path: Path):
        """preview = 头 500 字符固定(超长只取头,不随实现漂移)。

        文本用数字+逗号分隔(避免 config.redact 对 ≥32 位连续 alnum 疑似密钥
        打码,保证写盘内容=原文,纯测 preview 截断)。
        """
        ctx = make_ctx(tmp_path)
        text = ("0123456789," * 500)[:5000]               # 5000 字符,无 32+ 连串
        meta = await spill.put(text, kind="read", ctx=ctx)
        assert meta["preview"] == text[:500]
        assert meta["chars"] == 5000

    async def test_unique_names_per_put(self, tmp_path: Path):
        """多次 put → 文件名唯一(uuid 8hex),各文件各自原文。"""
        ctx = make_ctx(tmp_path)
        m1 = await spill.put("first", kind="exec", ctx=ctx)
        m2 = await spill.put("second", kind="exec", ctx=ctx)
        assert m1["ref"] != m2["ref"]
        assert len(files_in(ctx.config)) == 2

    async def test_session_isolation(self, tmp_path: Path):
        """会话隔离:不同 sid 各落各的私有区,互不可见。"""
        ctx1 = make_ctx(tmp_path, sid=SID)
        ctx2 = make_ctx(tmp_path, sid=SID2)
        m1 = await spill.put("hello", kind="read", ctx=ctx1)
        m2 = await spill.put("world", kind="read", ctx=ctx2)
        assert m1["ref"].startswith(f"{SID}/") and m2["ref"].startswith(f"{SID2}/")
        assert spill_dir(ctx1.config, SID2).joinpath(m2["ref"].split("/", 1)[1]).exists()
        assert len(files_in(ctx1.config, SID)) == 1

    async def test_over_limit_raises_pers223(self, tmp_path: Path):
        """put >max_per_file_bytes → PERS-223 拒写(超限不落盘)。

        max_per_file_bytes 合法下限 1024(CFG §3.6 pydantic ge=1024)。
        """
        ctx = make_ctx(tmp_path, max_per_file=1024)
        with pytest.raises(PyHError) as ei:
            await spill.put("x" * 1025, kind="read", ctx=ctx)
        assert ei.value.code == "PERS-223"
        assert ei.value.ctx["chars"] == 1025 and ei.value.ctx["max"] == 1024
        assert files_in(ctx.config) == []                 # 拒写零副作用

    async def test_write_fail_raises_pers221(self, tmp_path: Path):
        """目录写失败 → PERS-221(统一映射,含 OSError)。"""
        blocker = tmp_path / "blocker"
        blocker.write_text("file", encoding="utf-8")
        ctx = make_ctx(tmp_path)
        ctx.config = Settings(storage={"spill_dir": str(blocker)})
        with pytest.raises(PyHError) as ei:
            await spill.put("some long text", kind="read", ctx=ctx)
        assert ei.value.code == "PERS-221"

    async def test_redact_applied_before_disk(self, tmp_path: Path):
        """出口脱敏(INV-09):含疑似密钥原文不落盘,文件为掩码后文本。"""
        ctx = make_ctx(tmp_path, redact=masker)
        secret = "token=sk-abcdefgh1234567890 end"
        meta = await spill.put(secret, kind="read", ctx=ctx)
        files = files_in(ctx.config)
        assert files[0].read_text(encoding="utf-8", newline="") == "token=sk-*** end"
        assert "sk-abcdefgh1234567890" not in files[0].read_text(encoding="utf-8")
        assert meta["chars"] == len("token=sk-*** end")   # 计数按脱敏后文本

    async def test_redact_fallback_module(self, tmp_path: Path):
        """ctx.redact 缺失时回落 config.redact(默认开启,疑似 32+ 位串打码)。"""
        ctx = make_ctx(tmp_path)                          # redact=None
        meta = await spill.put("key=" + "A" * 32, kind="read", ctx=ctx)
        disk = files_in(ctx.config)[0].read_text(encoding="utf-8", newline="")
        assert "A" * 32 not in disk                       # 已打码
        assert meta["preview"].endswith("****")

    async def test_redact_disabled_writes_raw(self, tmp_path: Path):
        """log.redact_enabled=false → 出口策略关,原文直写(配置层承诺)。"""
        ctx = make_ctx(tmp_path)
        ctx.config = Settings(storage={"spill_dir": str(cfg_root(ctx.config))},
                              log={"redact_enabled": False})
        raw = "key=" + "B" * 32
        await spill.put(raw, kind="read", ctx=ctx)
        assert files_in(ctx.config)[0].read_text(
            encoding="utf-8", newline="") == raw

    async def test_put_requires_str(self, tmp_path: Path):
        """非 str 输入契约拒(PERS-221,Consumer 须先 render)。"""
        ctx = make_ctx(tmp_path)
        with pytest.raises(PyHError) as ei:
            await spill.put(12345, kind="read", ctx=ctx)  # type: ignore[arg-type]
        assert ei.value.code == "PERS-221"


# ===================================================================== read
class TestRead:
    async def test_default_window_and_more(self, tmp_path: Path):
        """G2:read(ref) 默认 start=0/limit=200 → 前 200 行 + more=true。"""
        ctx = make_ctx(tmp_path)
        text = make_long_text(250)
        meta = await spill.put(text, kind="read", ctx=ctx)
        out = await spill.read(meta["ref"], ctx=ctx)
        assert out["start"] == 0 and out["returned"] == spill.DEFAULT_LIMIT
        assert out["more"] is True                       # 250 行 > 200:还有后续
        assert out["content"] == "\n".join(text.split("\n")[:200])
        assert out["content"].startswith("L0000")

    async def test_window_slice(self, tmp_path: Path):
        """按行区间 [start, start+limit) 精确切片。"""
        ctx = make_ctx(tmp_path)
        meta = await spill.put(make_long_text(300), kind="read", ctx=ctx)
        out = await spill.read(meta["ref"], start=100, limit=50, ctx=ctx)
        assert out["returned"] == 50 and out["more"] is True
        assert out["content"].startswith("L0100") and out["content"].endswith("L0149")

    async def test_full_roundtrip(self, tmp_path: Path):
        """全文取回:content 拼回 == 原文(截断不吞事实,可完整取回)。"""
        ctx = make_ctx(tmp_path)
        text = make_long_text(9, prefix="line")
        meta = await spill.put(text, kind="read", ctx=ctx)
        out = await spill.read(meta["ref"], start=0, limit=1000, ctx=ctx)
        assert out["returned"] == 9 and out["more"] is False
        assert out["content"] == text

    async def test_trailing_newline_roundtrip(self, tmp_path: Path):
        """尾随换行:put lines 口径(count(\"\\n\")+1)与 read 切分严格同源。"""
        ctx = make_ctx(tmp_path)
        text = "a\nb\nc\n"                               # 4 行(含尾空行)
        meta = await spill.put(text, kind="read", ctx=ctx)
        assert meta["lines"] == 4
        out = await spill.read(meta["ref"], start=0, limit=10, ctx=ctx)
        assert out["content"] == text                    # roundtrip 无损

    async def test_empty_text(self, tmp_path: Path):
        """空文本:落盘空文件,read 返回空 content、more=false。

        read 行口径与 put 同源(count("\\n")+1):空文本 = 1 个逻辑行(空行),
        returned=1 与 meta.lines 一致(偏离 3:非 splitlines 尾行语义)。
        """
        ctx = make_ctx(tmp_path)
        meta = await spill.put("", kind="read", ctx=ctx)
        assert meta["chars"] == 0 and meta["lines"] == 1 and meta["preview"] == ""
        out = await spill.read(meta["ref"], ctx=ctx)
        assert out["content"] == "" and out["more"] is False
        assert out["returned"] == meta["lines"]           # 与写入口径一致

    async def test_read_past_eof(self, tmp_path: Path):
        """start 越过末尾:空返回不报错。"""
        ctx = make_ctx(tmp_path)
        meta = await spill.put("only", kind="read", ctx=ctx)
        out = await spill.read(meta["ref"], start=99, limit=10, ctx=ctx)
        assert out["content"] == "" and out["more"] is False and out["returned"] == 0

    async def test_counter_bumped_per_read(self, tmp_path: Path):
        """防循环烧预算:每次 read 的返回字符数累入 spill_read_chars。"""
        ctx = make_ctx(tmp_path, counters=FakeCounters())
        meta = await spill.put(make_long_text(250), kind="read", ctx=ctx)
        c1 = await spill.read(meta["ref"], start=0, limit=100, ctx=ctx)
        assert ctx.counters.value("spill_read_chars") == len(c1["content"])
        c2 = await spill.read(meta["ref"], start=100, limit=100, ctx=ctx)
        assert ctx.counters.value("spill_read_chars") == len(c1["content"]) + len(c2["content"])
        c3 = await spill.read(meta["ref"], start=200, limit=50, ctx=ctx)
        assert ctx.counters.value("spill_read_chars") == (
            len(c1["content"]) + len(c2["content"]) + len(c3["content"]))

    async def test_counter_dict_form(self, tmp_path: Path):
        """counters 为 dict 形态同样累加(装配容错)。"""
        ctx = make_ctx(tmp_path, counters={})
        meta = await spill.put(make_long_text(30), kind="read", ctx=ctx)
        out = await spill.read(meta["ref"], start=0, limit=10, ctx=ctx)
        assert ctx.counters["spill_read_chars"] == len(out["content"])

    async def test_no_counter_ok(self, tmp_path: Path):
        """counters 未接线:read 正常,不抛(防循环数据源缺失仅降级)。"""
        ctx = make_ctx(tmp_path)
        meta = await spill.put(make_long_text(30), kind="read", ctx=ctx)
        out = await spill.read(meta["ref"], ctx=ctx)
        assert out["returned"] == 30

    # ---- containment / 越权零读取(PERS-222)
    @pytest.mark.parametrize("bad_ref", [
        "/etc/passwd",                       # 绝对路径
        "C:/Windows/win.ini",                # 盘符绝对
        f"../{SID}/spill-1.txt",             # .. 逃逸
        "..",                                # 裸 ..
        "spill-rootless.txt",                # 首段非会话目录
    ])
    async def test_escape_zero_read(self, tmp_path: Path, bad_ref: str):
        """越权 ref → PERS-222 零读取(同 call 无部分返回,文件已存在也拒)。"""
        ctx = make_ctx(tmp_path)
        await spill.put("data" * 100, kind="read", ctx=ctx)   # 先造出真实文件
        with pytest.raises(PyHError) as ei:
            await spill.read(bad_ref, ctx=ctx)
        assert ei.value.code == "PERS-222"

    async def test_cross_session_ref_denied(self, tmp_path: Path):
        """防跨会话越权:本会话 ref 只指本会话 spill 文件(偏离 2 补强)。"""
        ctx1 = make_ctx(tmp_path, sid=SID)
        ctx2 = make_ctx(tmp_path, sid=SID2)
        m2 = await spill.put("secret-of-other-session", kind="read", ctx=ctx2)
        # 文件确实存在于其他会话私有区(他方 put 成功)
        assert spill_dir(ctx2.config, SID2).joinpath(m2["ref"].split("/", 1)[1]).exists()
        with pytest.raises(PyHError) as ei:
            await spill.read(m2["ref"], ctx=ctx1)          # 本会话读他会话 ref
        assert ei.value.code == "PERS-222"
        assert "sk-" not in ei.value.ctx.get("ref", "")    # ctx 不泄内容(只泄 ref)

    async def test_symlink_escape_zero_read(self, tmp_path: Path):
        """符号链接解析越界 → PERS-222(无权限环境自动 skip)。"""
        outside = tmp_path / "outside.txt"
        outside.write_text("secret", encoding="utf-8")
        ctx = make_ctx(tmp_path)
        await spill.enter(ctx)
        link = spill_dir(ctx.config) / "spill-evil.txt"
        try:
            link.symlink_to(outside)
        except OSError:
            pytest.skip("无符号链接权限(Windows 需管理员/开发者模式)")
        with pytest.raises(PyHError) as ei:
            await spill.read(f"{SID}/spill-evil.txt", ctx=ctx)
        assert ei.value.code == "PERS-222"

    async def test_invalid_ref_file_missing(self, tmp_path: Path):
        """ref 形状合法但文件不存在(已清理/会话结束)→ TLB-802 回喂重跑。"""
        ctx = make_ctx(tmp_path)
        await spill.enter(ctx)
        with pytest.raises(PyHError) as ei:
            await spill.read(f"{SID}/spill-00000000.txt", ctx=ctx)
        assert ei.value.code == "TLB-802"

    async def test_ref_after_purge_is_tlb802(self, tmp_path: Path):
        """生命周期清理后 ref 再读 → TLB-802(文件随会话删除)。"""
        ctx = make_ctx(tmp_path)
        meta = await spill.put("gone soon", kind="read", ctx=ctx)
        await spill.purge_session(SID, ctx)
        with pytest.raises(PyHError) as ei:
            await spill.read(meta["ref"], ctx=ctx)
        assert ei.value.code == "TLB-802"

    @pytest.mark.parametrize("start,limit", [(-1, 10), (0, 0), (0, 1001), (0, -5)])
    async def test_bad_range_tlb803(self, tmp_path: Path, start: int, limit: int):
        """start<0 / limit 越界 → TLB-803 零执行(契约层兜底)。"""
        ctx = make_ctx(tmp_path)
        meta = await spill.put(make_long_text(20), kind="read", ctx=ctx)
        with pytest.raises(PyHError) as ei:
            await spill.read(meta["ref"], start=start, limit=limit, ctx=ctx)
        assert ei.value.code == "TLB-803"

    async def test_non_str_ref_denied(self, tmp_path: Path):
        """非 str ref → PERS-222(containment 单点拒绝)。"""
        ctx = make_ctx(tmp_path)
        await spill.put("x", kind="read", ctx=ctx)
        with pytest.raises(PyHError) as ei:
            await spill.read(None, ctx=ctx)  # type: ignore[arg-type]
        assert ei.value.code == "PERS-222"


# ===================================================================== detach
class _LiteLocator:
    """ctx.storage 定位器替身(mount/unmount/_unmounted,seam §3 形态)。"""

    def __init__(self) -> None:
        self.services: dict[str, object] = {}
        self.calls: list[str] = []

    def mount(self, key: str, obj: object) -> None:
        self.services[key] = obj
        self.calls.append(f"mount:{key}")

    def unmount(self, key: str) -> None:
        self.services.pop(key, None)
        self.calls.append(f"unmount:{key}")

    def _unmounted(self, key: str) -> bool:
        return key not in self.services


class _RecordingTools:
    """ctx.tools 替身:记录 unregister 调用(可注入 TLB-802 双摘场景)。"""

    def __init__(self, registered: bool = True) -> None:
        self.unregister_calls: list[str] = []
        self.registered = registered

    def unregister(self, name: str) -> None:
        self.unregister_calls.append(name)
        if not self.registered:
            raise_code_tlb802(name)

    def has(self, name: str) -> bool:  # pragma: no cover 仅断言辅助
        return name in self.unregister_calls and self.registered


def raise_code_tlb802(name: str) -> None:
    from pyharness.errors import raise_code
    raise_code("TLB-802", tool=name, advice="不存在,无需注销")


class TestDetach:
    async def test_detach_keeps_files(self, tmp_path: Path):
        """detach 幂等摘除且**文件不删**(F060 repair/归档读窗口)。"""
        ctx = make_ctx(tmp_path)
        await spill.put("keep me", kind="read", ctx=ctx)
        files_before = files_in(ctx.config)
        assert files_before
        await spill.detach(ctx)
        assert files_in(ctx.config) == files_before      # 文件原样保留

    async def test_detach_unregisters_tool_and_unmounts(self, tmp_path: Path):
        """摘读工具 + 摘定位器(ctx.storage.unmount)。"""
        locator = _LiteLocator()
        tools = _RecordingTools()
        ctx = make_ctx(tmp_path, tools=tools, storage=locator)
        locator.mount("spill", object())                 # 已挂载(非已摘)
        await spill.detach(ctx)
        assert tools.unregister_calls == [TOOL]          # 读工具已摘
        assert "spill" not in locator.services           # 定位器已摘
        assert "unmount:spill" in locator.calls

    async def test_detach_idempotent_twice(self, tmp_path: Path):
        """双 detach 无副作用(第二次幂等早退,零异常)。"""
        locator = _LiteLocator()
        tools = _RecordingTools()
        ctx = make_ctx(tmp_path, tools=tools, storage=locator)
        locator.mount("spill", object())
        await spill.detach(ctx)
        await spill.detach(ctx)                          # 幂等:不抛不重复摘
        assert tools.unregister_calls == [TOOL]

    async def test_detach_unregister_already_gone(self, tmp_path: Path):
        """读工具已被摘(TLB-802)→ 静默幂等(不阻断卸载链)。"""
        locator = _LiteLocator()
        tools = _RecordingTools(registered=False)        # unregister 抛 TLB-802
        ctx = make_ctx(tmp_path, tools=tools, storage=locator)
        locator.mount("spill", object())
        await spill.detach(ctx)                          # 不抛
        assert "spill" not in locator.services

    async def test_detach_real_tools_registry(self, tmp_path: Path):
        """真实 ToolRegistry:detach 后 storage.spill 从工具表消失。"""
        registry = ToolRegistry()
        spill.register(registry)
        assert registry.has(TOOL)
        locator = _LiteLocator()
        locator.mount("spill", object())
        ctx = make_ctx(tmp_path, tools=registry, storage=locator)
        await spill.detach(ctx)
        assert not registry.has(TOOL)
        assert "spill" not in locator.services

    async def test_detach_no_spill_mounted(self, tmp_path: Path):
        """从未挂载(无痕迹)→ 幂等早退,不抛。"""
        ctx = make_ctx(tmp_path)                         # storage=None
        await spill.detach(ctx)
        ctx2 = make_ctx(tmp_path, storage=types.SimpleNamespace())  # 空 storage
        await spill.detach(ctx2)


# ===================================================================== register
class TestRegister:
    def test_registers_read_tool_only(self):
        """工具表只有读 storage.spill,无 put(LLM 可见面最小化,职责 7)。"""
        registry = ToolRegistry()
        names = spill.register(registry)
        assert names == [TOOL]
        assert registry.names() == [TOOL]                # 无 put/其他工具

    def test_definition_contract(self):
        """五要素:name/danger/description/schema + owner(spine)。"""
        registry = ToolRegistry()
        spill.register(registry)
        defn = registry.lookup(TOOL)
        assert defn.name == TOOL
        assert defn.danger == "none"
        assert defn.owner == "spine"                     # 脊柱支撑服务
        assert defn.timeout_s == 15
        assert len(defn.description) <= 200
        assert defn.ctx_path == "storage.spill"

    def test_schema_for_llm(self):
        """LLM schema:ref 必填、start≥0、limit 1-1000 默认 200。"""
        registry = ToolRegistry()
        spill.register(registry)
        schemas = registry.schemas_for(None)
        assert len(schemas) == 1
        fn = schemas[0]["function"]
        assert fn["name"] == TOOL
        params = fn["parameters"]
        assert params["required"] == ["ref"]
        assert params["properties"]["ref"]["type"] == "string"
        start = params["properties"]["start"]
        assert start["minimum"] == 0 and start["default"] == 0
        limit = params["properties"]["limit"]
        assert limit["minimum"] == 1 and limit["maximum"] == 1000
        assert limit["default"] == 200

    async def test_handle_executes_read(self, tmp_path: Path):
        """Provider.handle(args, ctx) 可执行(executor 关3 契约)。"""
        registry = ToolRegistry()
        spill.register(registry)
        ctx = make_ctx(tmp_path)
        meta = await spill.put(make_long_text(300), kind="read", ctx=ctx)
        provider = registry.lookup_provider(TOOL)
        out = await provider.handle({"ref": meta["ref"]}, ctx)
        assert out["returned"] == 200 and out["more"] is True
        # 参数经 validate_args 后 start/limit 带 schema 默认值(INV-06)
        args = registry.validate_args(TOOL, {"ref": meta["ref"]})
        assert args == {"ref": meta["ref"], "start": 0, "limit": 200}
        out2 = await provider.handle(args, ctx)
        assert out2["content"] == out["content"]

    async def test_handle_escape_propagates_pers222(self, tmp_path: Path):
        """handle 转发越权 ref → PERS-222(executor 转 tool.error 回喂)。"""
        registry = ToolRegistry()
        spill.register(registry)
        ctx = make_ctx(tmp_path)
        provider = registry.lookup_provider(TOOL)
        with pytest.raises(PyHError) as ei:
            await provider.handle({"ref": "../etc/passwd"}, ctx)
        assert ei.value.code == "PERS-222"

    def test_dup_register_rejected(self):
        """重名注册 → TLB-801(注册表零变更)。"""
        registry = ToolRegistry()
        spill.register(registry)
        from pyharness.core.spill import _read_definition
        with pytest.raises(PyHError) as ei:
            registry.register_tool(_read_definition())
        assert ei.value.code == "TLB-801"


# ===================================================================== purge
class TestPurge:
    async def test_purge_removes_session_dir(self, tmp_path: Path):
        """会话清理:spill 文件 + 空目录收口,随会话生命周期消失。"""
        ctx = make_ctx(tmp_path)
        for i in range(3):
            await spill.put(f"data-{i}", kind="read", ctx=ctx)
        d = spill_dir(ctx.config)
        assert d.exists() and len(files_in(ctx.config)) == 3
        await spill.purge_session(SID, ctx)
        assert not d.exists()                            # 目录已收口
        assert files_in(ctx.config) == []

    async def test_purge_removes_tmp_residual(self, tmp_path: Path):
        """残留 .tmp(中断写入)→ 一并清理,rmdir 可收口(偏离 5)。"""
        ctx = make_ctx(tmp_path)
        await spill.put("data", kind="read", ctx=ctx)
        d = spill_dir(ctx.config)
        (d / "spill-deadbeef.txt.tmp").write_text("half", encoding="utf-8")
        await spill.purge_session(SID, ctx)
        assert not d.exists()

    async def test_purge_nonexistent_idempotent(self, tmp_path: Path):
        """目录不存在 → 幂等,零异常。"""
        ctx = make_ctx(tmp_path)
        await spill.purge_session(SID, ctx)              # 从未 put:无目录
        await spill.purge_session(SID, ctx)              # 再来一次仍幂等

    async def test_purge_only_own_session(self, tmp_path: Path):
        """只删目标会话目录,他会话文件不受影响。"""
        ctx1 = make_ctx(tmp_path, sid=SID)
        ctx2 = make_ctx(tmp_path, sid=SID2)
        await spill.put("mine", kind="read", ctx=ctx1)
        await spill.put("theirs", kind="read", ctx=ctx2)
        await spill.purge_session(SID, ctx1)
        assert not spill_dir(ctx1.config, SID).exists()
        assert len(files_in(ctx2.config, SID2)) == 1     # 他会话完好

    @pytest.mark.parametrize("bad_sid", [
        "..",                  # 逃逸到根外
        "",                    # 空 = 目标即根
        "/tmp/evil",           # 绝对路径
        "../../outside",
    ])
    async def test_purge_escape_rejected(self, tmp_path: Path, bad_sid: str):
        """清理目标逃逸 spill 根 → PERS-222 拒清理(防御纵深),文件不动。"""
        ctx = make_ctx(tmp_path)
        await spill.put("keep", kind="read", ctx=ctx)
        with pytest.raises(PyHError) as ei:
            await spill.purge_session(bad_sid, ctx)
        assert ei.value.code == "PERS-222"
        assert len(files_in(ctx.config)) == 1            # 拒清理零副作用

    async def test_purge_delete_failure_only_warns(self, tmp_path: Path,
                                                   monkeypatch):
        """删除失败(占用/权限)→ 本地告警不阻断(残留 F060 兜底)。"""
        ctx = make_ctx(tmp_path)
        await spill.put("data", kind="read", ctx=ctx)

        def _boom(*_a, **_k):
            raise OSError("permission denied")

        monkeypatch.setattr(Path, "unlink", _boom)
        await spill.purge_session(SID, ctx)              # 不抛,仅日志


# ===================================================================== 生命周期
class TestLifecycle:
    async def test_purge_on_session_finished(self, tmp_path: Path):
        """订阅 session.finished → 会话结束后 spill 目录删除(PRD F039)。"""
        bus = EventBus()
        bus.register_type("session.finished", None)
        ctx = make_ctx(tmp_path, bus=bus)
        await spill.put("data", kind="read", ctx=ctx)
        owner = spill.subscribe_purge(ctx, SID)
        assert owner == f"spill:{SID}"
        assert spill_dir(ctx.config).exists()
        await bus.emit("session.finished", {"session_id": SID})
        assert not spill_dir(ctx.config).exists()        # 随会话清理(I-3)

    async def test_other_session_finished_no_touch(self, tmp_path: Path):
        """他会话 finished 事件不动本会话目录(跨会话过滤)。"""
        bus = EventBus()
        bus.register_type("session.finished", None)
        ctx = make_ctx(tmp_path, bus=bus)
        await spill.put("data", kind="read", ctx=ctx)
        spill.subscribe_purge(ctx, SID)
        await bus.emit("session.finished", {"session_id": SID2})
        assert spill_dir(ctx.config).exists()            # 本会话目录仍在
        assert len(files_in(ctx.config)) == 1

    async def test_detach_unsubscribes_purge(self, tmp_path: Path):
        """detach 摘订阅后,finished 不再触发清理(文件留给 repair 窗口)。"""
        bus = EventBus()
        bus.register_type("session.finished", None)
        ctx = make_ctx(tmp_path, bus=bus)
        await spill.put("data", kind="read", ctx=ctx)
        spill.subscribe_purge(ctx, SID)
        await spill.detach(ctx)                          # 摘除(含订阅)
        await bus.emit("session.finished", {"session_id": SID})
        assert spill_dir(ctx.config).exists()            # 清理订阅已摘:文件保留
        assert len(files_in(ctx.config)) == 1

    async def test_provider_lifecycle(self, tmp_path: Path):
        """SpillProvider 全生命周期:enter(建目录+订阅)→ put/read → detach。"""
        bus = EventBus()
        bus.register_type("session.finished", None)
        ctx = make_ctx(tmp_path, bus=bus)
        prov = spill.SpillProvider(ctx)
        d = await prov.enter(ctx)
        assert d == spill_dir(ctx.config).resolve()
        meta = await prov.put("provider data", kind="read")
        out = await prov.read(meta["ref"], start=0, limit=10)
        assert out["content"] == "provider data"
        await prov.detach(ctx)
        # detach 不删文件(读窗口),清理只随 session.finished 订阅
        assert spill_dir(ctx.config).exists()

    async def test_provider_handle_roundtrip_through_executor_style(self,
                                                                    tmp_path: Path):
        """Storage.spill 经 executor 契约(handle+validate_args)端到端可用。"""
        registry = ToolRegistry()
        spill.register(registry)
        ctx = make_ctx(tmp_path)
        text = make_long_text(12, prefix="row")
        provider = spill.SpillProvider(ctx)
        meta = await provider.put(text, kind="fs.read_file")
        args = registry.validate_args(TOOL, {"ref": meta["ref"],
                                             "start": 0, "limit": 5})
        out = await registry.lookup_provider(TOOL).handle(args, ctx)
        assert out["returned"] == 5 and out["more"] is True
        assert out["content"] == "\n".join(text.split("\n")[:5])
