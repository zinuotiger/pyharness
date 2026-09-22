"""INV-09 出口脱敏(F016):**全出口**不得出现 32+ 位疑似密钥原文。

2026-09-21 R10 修复前的实测缺陷:`ctx.redact`(F016 单口)被 tool_fs / tool_web /
spill 三处读取,但**全库没有任何装配点注入它** ⇒ 生产恒缺失,fs/web 出口
**静默降级为原样返回** —— `fs.read_file` 读到含 `sk-AAAA…` 的文件时,密钥原文
进入 `tool.result`(进而进事件日志、FTS 索引与 LLM 上下文),违反 SECURITY §6.4
"全出口覆盖=事件 payload/错误消息/tool.result summary/spill/PTY/日志"。

证据口径(本目录纪律):不只断言字段/日志,而是**走真实工具链路**后检查
`tool.result` 载荷与**整条事件流**里是否出现原文;并附"撤掉注入即失败"的鉴别力说明。
"""
from __future__ import annotations

import pathlib

SECRET = "sk-" + "A" * 40          # 32+ 位疑似密钥(命中 config.redact 的通用形态)


async def test_fs_output_is_redacted_end_to_end(e2e_factory):
    """真实链路(脚本化 LLM → 四关管道 → fs.read_file → tool.result)不得泄原文。

    鉴别力:撤掉 `create_agent` 里的 `ctx.redact` 注入 → 本用例失败(实测原样输出
    `API_KEY=sk-AAAA…`)。
    """
    s = await e2e_factory(
        [{"id": "c1", "name": "fs.read_file", "args": {"path": "cfg.txt"}}],
        sid="s-sec-redact-01")
    ws = pathlib.Path(s.ctx.scope.policy.workspace_root)
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "cfg.txt").write_text(f"API_KEY={SECRET}\n", encoding="utf-8")
    await s.boot()
    assert (await s.run("read cfg")).ok

    results = s.of("tool.result")
    assert results, "前置:应有 tool.result"
    summary = str([e.payload.get("summary") for e in results])
    assert SECRET not in summary, f"tool.result 泄漏密钥原文:{summary[:200]}"
    assert "sk-AAAAAA***" in summary, f"应打码为 sk-<前6位>***:{summary[:200]}"
    # 更强的口径:整条事件流(含落盘真源)都不得出现原文
    assert all(SECRET not in str(e.payload) for e in s.events()), "事件流泄漏原文"


async def test_agent_ctx_exposes_redact_facade(e2e_factory):
    """装配面:agent ctx 必须带 `redact`(F016 单口)—— 三个消费方都读它。"""
    s = await e2e_factory(
        [{"id": "c1", "name": "fs.list_dir", "args": {"path": "."}}],
        sid="s-sec-redact-02")
    await s.boot()
    assert (await s.run("list")).ok                  # 首轮 wake 才建 agent ctx
    ag = s.ctx.engine_spine.active_agents.get(s.ctx.session.sid)
    assert ag is not None and hasattr(ag.ctx, "redact"), \
        "未注入 ctx.redact ⇒ fs/web 出口会静默不脱敏"
    assert SECRET not in ag.ctx.redact(f"k={SECRET}")
    assert "k=" in ag.ctx.redact(f"k={SECRET}")          # 只打码密钥,不吞其余内容


def test_redact_can_be_disabled_by_config():
    """配置层承诺的逃生口:`log.redact_enabled=False` ⇒ 恒等(原样),且**仅**此开关。"""
    from pyharness.config import DEFAULTS, Settings, redactor_of

    raw = Settings.model_validate(DEFAULTS).model_dump()
    raw["log"]["redact_enabled"] = False
    off = redactor_of(Settings.model_validate(raw))
    assert off(f"k={SECRET}") == f"k={SECRET}"           # 显式关闭:不打码
    raw["log"]["redact_enabled"] = True
    on = redactor_of(Settings.model_validate(raw))
    assert SECRET not in on(f"k={SECRET}")


# ============ R11-7:配置面解析必须认 ctx.config(装配点),不止 ctx.cfg
async def test_cfg_alias_is_honored_by_tool_fs(e2e_factory):
    """**修复前失败**:`tool_fs._cfg_get` 只认 `ctx.cfg`(装配层设的是 `ctx.config`)。

    ⇒ 本模块**所有配置阈值恒回落默认**(`loop.content.file_spill_bytes` 读阈值、
    `security.policy.read_extra_dirs` 例外清单)。同族三处一直兼容两名字,仅本处
    漏一处判据(同类第五次)。此处以 `file_spill_bytes` 为可观测面:
    设 1024 后,读一个 >1024B 的文本必须走 spill(返回 ref/摘要而非全文)。
    """
    s = await e2e_factory(None, sid="s-cfg-alias-0001")
    s.ctx.settings.loop.content.file_spill_bytes = 1024     # 极小阈值 ⇒ 必走 spill
    await s.boot()
    assert (await s.run("list")).ok                          # 首轮建 agent ctx

    from pyharness.core import tool_fs
    ws = pathlib.Path(s.ctx.scope.policy.workspace_root)
    (ws / "big.txt").write_text("x" * 4096, encoding="utf-8")
    ag = s.ctx.engine_spine.active_agents.get(s.ctx.session.sid)
    out = await tool_fs.read_file({"path": "big.txt"}, ag.ctx)
    assert out.get("spill_ref") or out.get("truncated"), \
        f"配置阈值未生效(修复前 ctx.cfg 缺失 ⇒ 恒用 65536 默认):{str(out)[:200]}"


async def test_read_extra_dirs_is_read_only_exception(e2e_factory, tmp_path):
    """`security.policy.read_extra_dirs` = workspace 外**只读**例外(A2/R24 已接线)。

    修复前(见 L-24)该能力**结构性不可达**:g3 只按 workspace 几何判定、provider 对
    绝对路径先抛 POL-FS-1 ⇒ 运维配了例外**仍被拒**。现两层同判:

      ① 例外目录内的文件 **read_file 允许**(能力可达);
      ② 同目录 **写仍然拒**(POL-FS-1)——例外是**只读**的,不扩大写面;
      ③ 未列入例外的界外路径 **仍拒**(不放宽默认姿态)。
    """
    outside = tmp_path / "outside"
    outside.mkdir(parents=True, exist_ok=True)
    (outside / "shared.txt").write_text("shared", encoding="utf-8")
    stranger = tmp_path / "stranger"
    stranger.mkdir(parents=True, exist_ok=True)
    (stranger / "x.txt").write_text("x", encoding="utf-8")

    s = await e2e_factory(None, sid="s-sec-extradir-2")
    s.ctx.settings.security.policy.read_extra_dirs = [str(outside)]
    await s.boot()
    assert (await s.run("list")).ok

    from pyharness.core import tool_fs
    from pyharness.errors import PyHError
    ag = s.ctx.engine_spine.active_agents.get(s.ctx.session.sid)

    # ① 例外目录内:读放行(能力可达)
    out = await tool_fs.read_file({"path": str(outside / "shared.txt")}, ag.ctx)
    assert "shared" in str(out), f"例外目录读应放行:{str(out)[:200]}"

    # ② 例外是**只读**的:同目录写仍拒
    try:
        await tool_fs.write_file({"path": str(outside / "new.txt"),
                                  "content": "x"}, ag.ctx)
        raise AssertionError("例外目录不得放宽**写**面")
    except PyHError as e:
        assert e.code == "GRD-401" and e.ctx.get("reason") == "POL-FS-1", e.ctx

    # ③ 未列入例外的界外路径仍拒
    try:
        await tool_fs.read_file({"path": str(stranger / "x.txt")}, ag.ctx)
        raise AssertionError("未列入例外的界外路径必须仍拒")
    except PyHError as e:
        assert e.code == "GRD-401", e.ctx


def test_cfg_accessors_accept_config_alias_statically():
    """**静态不变量**:所有"读 ctx 配置"的助手都必须兼容 `config`(装配点名)。

    修复前:`tool_fs` 只认 `cfg`,同族三处(session_query/spill/tool_web)认两名字
    —— 同一判据多处实现、漏一处即静默失效(同类缺陷第五次)。
    """
    from pathlib import Path as _P
    root = _P(__file__).resolve().parents[2] / "pyharness"
    import re
    offenders = []
    for rel in ("core/tool_fs.py", "core/tool_web.py", "core/spill.py",
                "core/session_query.py"):
        src = (root / rel).read_text(encoding="utf-8")
        # 该文件若出现单名读取 "cfg" 而不含 "config" 兜底 → 违规
        if re.search(r'getattr\([^)]*,\s*"cfg"\s*,', src) and '"config"' not in src:
            offenders.append(rel)
    assert not offenders, f"配置面读取未兼容 config 别名:{offenders}"
