"""不变量:Declared Config Is Reachable（声明配置必须可达）—— N1。

**性质**:任何在 ``Settings`` 中**声明并校验**的配置键,必须存在 ≥1 条从该键到
运行对象的赋值路径;否则它是"死配置"(运维改了不生效,却看起来可调)。

**机制**:``open_store`` 接收**已解析标量**(不 import config),装配层经
``flush_kwargs_of(cfg)`` 取值注入。

**证据**:修复前实测 —— 配置 ``interval=3.0 / batch=7``,store 实为 ``0.5 / 64``。

**负向**:断开任一装配点的 ``flush_kwargs_of`` 注入 ⇒ 本文件必须 RED。
"""
from __future__ import annotations

import ast
import functools
import pathlib

import pytest

from pyharness.config import Settings, load_settings
from pyharness.persistence import open_store

ROOT = pathlib.Path(__file__).resolve().parents[2]
KEYS = ("flush_interval_s", "flush_batch")


# ---------------------------------------------------------------- 运行时可达
@pytest.mark.parametrize("interval,batch", [(3.0, 7), (0.05, 1), (10.0, 1024)])
def test_configured_value_equals_runtime_value(tmp_path, interval, batch):
    """**核心断言**:配置值 == 运行时值(经**生产路径**:open_store + flush_kwargs_of)。"""
    from pyharness.persistence import flush_kwargs_of

    cfg = load_settings()
    cfg.log.jsonl.flush_interval_s = interval
    cfg.log.jsonl.flush_batch = batch
    store = open_store("s-invcfg-000001", dir=tmp_path,
                       **flush_kwargs_of(cfg))
    try:
        assert store.flush_interval_s == interval, \
            f"配置 {interval} 未生效,实际 {store.flush_interval_s}"
        assert store.flush_batch == batch, \
            f"配置 {batch} 未生效,实际 {store.flush_batch}"
    finally:
        store.close()


def test_defaults_unchanged_when_cfg_none(tmp_path):
    """默认值行为未被破坏:不传参 ⇒ 回落模块常量 DEFAULTS(0.5 / 64)。"""
    store = open_store("s-invcfg-000002", dir=tmp_path)
    try:
        assert store.flush_interval_s == 0.5
        assert store.flush_batch == 64
    finally:
        store.close()


def test_flush_kwargs_of_is_duck_typed():
    """助手对缺失/畸形 cfg 返回 ``{}``(不抛)—— 保持"未装配=默认"语义。"""
    from pyharness.persistence import flush_kwargs_of
    assert flush_kwargs_of(None) == {}
    assert flush_kwargs_of(object()) == {}


# ---------------------------------------------------------------- 静态可达性
@pytest.mark.parametrize("key", KEYS)
def test_declared_key_has_a_reader_outside_config(key):
    """静态面:声明键必须在 ``config.py`` **之外**至少有读者。"""
    hits = []
    for p in sorted((ROOT / "pyharness").rglob("*.py")):
        if p.name == "config.py":
            continue
        if key in p.read_text(encoding="utf-8"):
            hits.append(str(p.relative_to(ROOT)).replace("\\", "/"))
    assert hits, f"{key} 在 config.py 之外零命中 ⇒ 死配置(声明可调但无读取者)"


# ------------------------------------------------- 全量声明键:死配置防线(N1 泛化)
# 2026-09-21 扫描:97 个叶子键中 15 个在 ``config.py`` 之外**零读取者**。它们全部在
# ``CFG.md`` 里被写成可调旋钮(含 env 映射),运维改了却不生效 —— 即"声明配置必须可达"
# 的反例。其中除 ``schema_version``(config 内部版本字段)外,均对应 PRD-Core §7
# **未勾选**的功能项(F005/F017/F022/F054/F055/F061/F063 与 DIS-SEAM §3.3/3.4)。
#
# 本清单是**显式登记**:新出现的死配置会让下面两个用例变红,强制做一次有意识的决定
# (接线 / 从 config 与 CFG.md 移除 / 追加到此表并写明理由)。**不得**静默扩充。
KNOWN_UNIMPLEMENTED: dict[str, str] = {
    "schema_version": "config 内部版本字段(迁移锚),无外部读者属预期",
    "step_timeout_s": "冗余声明:F017 的时间界已由**工具级** defn.timeout_s 承载"
                      "(executor 关3 的 TLB-805);loop 级外圈闸未实现(见下条同类)",
    "tool_concurrency": "**与 F026 语义冲突,非缺读取点**:同轮多 tool_calls 的"
                        "'连败 2 次即终止本轮后续调用'是**顺序**语义,并发执行无法在"
                        "先验上判定'后续'是否该跑;接线=重定义该安全规则(需产品裁定)",
    "thread_pool_size": "同上:同步 Provider 现按'每次调用独立单工池'实现(max_workers=1),"
                        "与上条的并发语义绑定,一并待裁定",
    "proc_mem_limit_mb": "OS 级限额:POSIX 可 setrlimit(RLIMIT_AS),Windows 无对应原语"
                         "且本机为 Windows(不可验证的代码不写);实现须先定平台策略",
    "cache_ttl_s": "**缺子系统**:仓库无凭据缓存层(凭据每次解析即用即弃),"
                   "TTL 是缓存属性,须先有缓存",
    "ctx_lazy": "**默认值(true)即当前实现**;false 的'启动期全量 enter'需要 agent ctx,"
                "而 agent 在装配期尚未创建(首个 wake 才建)⇒ 非缺读取点,是装配序问题",
    "pre_activate": "同上:预激活清单的对象是**会话级 provider**,装配期无 agent ctx 可挂",
}


# 名字撞车的键:确实是死配置,但其**字段名**与生产里某个无关的关键字实参同名时,
# 自动判据无法区分 ⇒ 只能靠人工复核,故不参与复活判定。
# 2026-09-21 现状:**空**(此前唯一的 `max_bytes` 已随 F061 接线转正,见 R6)。
NAME_COLLISION_DEAD: dict[str, str] = {}


async def test_bus_backpressure_limit_is_wired(tmp_path):
    """F005:`plugins.backpressure_limit` 配置值 == 装配出的总线实际阈值。

    修复前实测:engine / cli / 子会话三处装配点一律 ``EventBus()`` 取默认 1000,
    配置键(带 1-100000 范围校验 + CFG 声明)没有任何读取点。
    """
    from pyharness.bus import EventBus
    from pyharness.engine import build_spine

    cfg = _cfg(tmp_path)
    cfg.plugins.backpressure_limit = 7
    spine = await build_spine(cfg, sid="s-invcfg-000010",
                              sessions_dir=tmp_path / "sessions")
    try:
        assert spine.bus.backpressure_limit == 7, "配置未生效到总线"
    finally:
        await spine.close()
    assert EventBus().backpressure_limit == 1000, "默认不可被破坏"


async def test_task_queue_max_is_wired(tmp_path):
    """F043:`loop.task_queue_max` 配置值 == 装配出的队列实际队深上限。"""
    from types import SimpleNamespace

    from pyharness.core.session import SessionLog
    from pyharness.engine import attach_engine_to_ctx

    cfg = _cfg(tmp_path)
    cfg.llm.api_key = "env:PH_INVCFG_TEST_KEY"     # 过 CRED-701 引用检查(不解析值)
    cfg.loop.task_queue_max = 5
    ctx = SimpleNamespace()
    log_ = SessionLog("s-invcfg-000011")
    spine = await attach_engine_to_ctx(ctx, cfg, log_=log_,
                                       sessions_dir=tmp_path / "sessions",
                                       preload=False)
    try:
        assert ctx.task_queue._max_queue == 5, "配置未生效到队列(仍硬编码 32)"
    finally:
        await spine.close()


def _cfg(tmp_path):
    """最小可用 Settings(路径全部指到 tmp;不读用户真实配置)。"""
    cfg = load_settings()
    cfg.storage.root = str(tmp_path)
    cfg.storage.sessions_dir = str(tmp_path / "sessions")
    cfg.storage.workspaces_dir = str(tmp_path / "workspaces")
    cfg.storage.spill_dir = str(tmp_path / "spill")
    cfg.storage.db_path = str(tmp_path / "index.db")
    return cfg


def _leaf_field_names() -> set[str]:
    """``Settings`` 全部**叶子**字段名(递归展开嵌套 BaseModel)。"""
    from pydantic import BaseModel

    out: set[str] = set()

    def walk(model) -> None:
        for name, fld in model.model_fields.items():
            ann = fld.annotation
            nested = ann if (isinstance(ann, type)
                             and issubclass(ann, BaseModel)) else None
            (walk(nested) if nested is not None else out.add(name))

    walk(Settings)
    return out


@functools.lru_cache(maxsize=1)
def _readers() -> dict[str, list[str]]:
    """一次性扫描 pyharness/(排除 config.py),汇总"名字 → 读取点"表。

    计三类读取形态:属性访问(``x.<name>``)、关键字实参名(``<name>=...``)、
    字符串常量(**含点分路径的末段**,如 ``"security.policy.read_extra_dirs"``)。
    **不扫注释与文档串**(只看 AST 节点)——旧的行文本判据正是被
    docstring/同名参数/同名属性骗过才漏检的。

    单次解析全库(键查询走内存表),避免逐键重复 parse(90 键 × 130 文件)。
    """
    out: dict[str, list[str]] = {}

    def _add(name_: str, where: str) -> None:
        out.setdefault(name_, []).append(where)

    for p in sorted((ROOT / "pyharness").rglob("*.py")):
        if p.name == "config.py":
            continue
        rel = str(p.relative_to(ROOT)).replace("\\", "/")
        tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                _add(node.attr, f"{rel}:{node.lineno}")
            elif isinstance(node, ast.keyword) and node.arg:
                _add(node.arg, f"{rel}:{node.lineno}")
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                for part in filter(None, node.value.split(".")):
                    _add(part, f"{rel}:{node.lineno}")
    return out


def _reader_hits(name: str) -> list[str]:
    """键名的读取点(判据与盲区见 ``_readers``)。仍只是**地板**,不是可达性证明。"""
    return _readers().get(name, [])


def test_no_undeclared_dead_config_key():
    """**N1 泛化**:任何声明键都必须有 config.py 之外的读者,或在 KNOWN_UNIMPLEMENTED 登记。

    口径说明:这是**文本级地板**(判据见 ``_reader_hits``),不是可达性证明;真正的
    可达性靠逐键的运行时断言(如 ``test_configured_value_equals_runtime_value``)
    与 ``LIMITATIONS.md`` L-20 的人工登记。零命中 ⇒ 无任何代码按名取值。
    """
    unregistered = [n for n in sorted(_leaf_field_names())
                    if n not in KNOWN_UNIMPLEMENTED and not _reader_hits(n)]
    assert not unregistered, (
        "以下声明键在 config.py 外零读取者,且未登记为已知未实现 ⇒ 死配置:"
        f"{unregistered};请接线、或从 config+CFG.md 移除、或追加到 KNOWN_UNIMPLEMENTED"
        " 并写明理由"
    )


def test_registered_dead_keys_are_still_dead():
    """登记表**不得腐烂**:已实现的键必须从表中移除(否则表会变成借口的堆积地)。

    ``NAME_COLLISION_DEAD`` 中的键由人工复核维持(自动判据无法区分同名撞车),
    仍需在接线时手工移出。
    """
    checkable = [k for k in KNOWN_UNIMPLEMENTED if k not in NAME_COLLISION_DEAD]
    resurrected = [k for k in checkable if _reader_hits(k)]
    assert not resurrected, (
        f"以下键已出现读取者(疑似已接线),应从 KNOWN_UNIMPLEMENTED 移除:{resurrected}")


async def test_f026_threshold_is_wired_from_config(tmp_path):
    """F026 连败阈值:**配置值 == 装配到执行器的运行时值**(2026-09-21 接线)。

    修复前实测:``loop.max_arg_failures_per_round``(声明 1-5,可经 env 调)零读取者,
    阈值恒硬编码 2 ⇒ 运维调它不生效(与 N1 flush 同型)。
    """
    from pyharness.engine import build_spine

    cfg = load_settings()
    cfg.storage.root = str(tmp_path)
    cfg.storage.sessions_dir = str(tmp_path / "sessions")
    cfg.storage.workspaces_dir = str(tmp_path / "workspaces")
    cfg.storage.spill_dir = str(tmp_path / "spill")
    cfg.storage.db_path = str(tmp_path / "index.db")
    cfg.loop.max_arg_failures_per_round = 4
    spine = await build_spine(cfg, sid="s-invcfg-000003",
                              sessions_dir=tmp_path / "sessions")
    try:
        assert spine.tools._max_arg_failures == 4, \
            "配置未生效到执行器(仍是硬编码)"
    finally:
        await spine.close()
    # 默认不可被破坏:不设配置时回落常量 2
    from pyharness.core.tools_executor import (DEFAULT_MAX_ARG_FAILURES,
                                               ToolExecutor)
    assert ToolExecutor()._max_arg_failures == DEFAULT_MAX_ARG_FAILURES == 2


# ==================================== F033 · 健康探针(回切的唯一驱动)可达性
class _ProbeAdapter:
    """探针替身:按脚本序列返回 ping 结果(``None`` = 成功,错误码 = 失败)。"""

    def __init__(self, results: list) -> None:
        self.results = list(results)
        self.calls = 0

    async def ping(self) -> float:
        from pyharness.errors import raise_code
        r = self.results[min(self.calls, len(self.results) - 1)]
        self.calls += 1
        if r is None:
            return 0.01
        raise_code(r, model="probe-stub")


async def test_llm_probe_is_started_and_recovers_primary(tmp_path):
    """**F033 可达性**:装配必须启动健康探针,且探针能把主模型**回切**回来。

    修复前实测:``FallbackChain.probe_loop`` 在 pyharness/ 内**零生产调用者**(只有
    单测启动过)。而健康状态机只能被探针改回 ``healthy``、降级链 ``idx`` 也只在探针里
    归零 ⇒ **一次降级 = 进程生命周期内永久降级**,``llm.recovered`` 永不发出。
    同时证明 ``llm.probe.interval_s`` 已接线(周期取自配置,此处 0.02s)。
    """
    import asyncio
    import time as _time

    from pyharness.core import llm as llm_mod
    from pyharness.engine import build_spine

    cfg = load_settings()
    cfg.storage.root = str(tmp_path)
    cfg.storage.sessions_dir = str(tmp_path / "sessions")
    cfg.storage.workspaces_dir = str(tmp_path / "workspaces")
    cfg.storage.spill_dir = str(tmp_path / "spill")
    cfg.storage.db_path = str(tmp_path / "index.db")
    cfg.llm.model = "probe-main"
    cfg.llm.fallback_models = ["probe-backup"]
    cfg.llm.probe.interval_s = 0.02                   # 配置驱动:探针周期

    saved = dict(llm_mod.adapters)
    llm_mod.adapters.clear()
    llm_mod.adapters.update({
        "probe-main": _ProbeAdapter(["LLM-303"] * 3 + [None] * 40),
        "probe-backup": _ProbeAdapter([None] * 40)})
    spine = None
    try:
        spine = await build_spine(cfg, sid="s-invprobe1",
                                  sessions_dir=tmp_path / "sessions")
        assert spine.llm_probe is not None, "配置了 fallback_models ⇒ 必须启动探针"
        chain = spine.llm.chain
        assert chain is not None, "LLMClient.chain 只读别名应可见(否则无法装配探针)"

        chain.idx = 1                                  # 模拟已降级到备用
        deadline = _time.time() + 10.0
        while _time.time() < deadline and chain.health["probe-main"].state != "down":
            await asyncio.sleep(0.02)
        assert chain.health["probe-main"].state == "down", \
            "主模型连 3 败应进 down(探针确实在跑)"
        while _time.time() < deadline and chain.idx != 0:
            await asyncio.sleep(0.02)
        assert chain.idx == 0, "主模型 2 好必须自动回切(idx 归零)"
        assert chain.health["probe-main"].state == "healthy"
    finally:
        if spine is not None:
            await spine.close()
            assert spine.llm_probe is None, "收尾必须取消探针任务(不留悬挂任务)"
        llm_mod.adapters.clear()
        llm_mod.adapters.update(saved)


async def test_llm_probe_absent_without_fallback_models(tmp_path):
    """负空间:未配置 ``fallback_models`` ⇒ 无降级链 ⇒ **不得**起探针任务。"""
    from pyharness.engine import build_spine

    cfg = load_settings()
    cfg.storage.root = str(tmp_path)
    cfg.storage.sessions_dir = str(tmp_path / "sessions")
    cfg.storage.workspaces_dir = str(tmp_path / "workspaces")
    cfg.storage.spill_dir = str(tmp_path / "spill")
    cfg.storage.db_path = str(tmp_path / "index.db")
    cfg.llm.fallback_models = []
    spine = await build_spine(cfg, sid="s-invprobe2",
                              sessions_dir=tmp_path / "sessions")
    try:
        assert spine.llm.chain is None and spine.llm_probe is None, \
            "无链不得起探针(空转任务=资源噪声)"
    finally:
        await spine.close()


def test_open_store_takes_scalars_not_a_config_object():
    """依赖方向:``open_store`` 只接收**已解析标量**,不接收 config 对象。

    注:``persistence`` 早已 ``from pyharness.config import DEFAULTS``(模块级**静态
    常量字典**,用于零配置回落)——这是既有的、有意的依赖,本断言**不**反对它;
    反对的是让工厂去读**活的 Settings 对象**(那会把"解析配置"的职责下沉到持久化层,
    与装配层负责解析的设计相悖)。
    """
    import inspect

    sig = inspect.signature(open_store)
    bad = [p for p in sig.parameters
           if p not in ("session_id", "dir", "flush_interval_s", "flush_batch")]
    assert not bad, f"open_store 出现非标量入参:{bad}"

    src = (ROOT / "pyharness/persistence.py").read_text(encoding="utf-8")
    assert "import Settings" not in src, "persistence 不得依赖 Settings 模型"
    # DEFAULTS 是静态回落表(既有设计),允许;Settings 是活配置,禁止
    assert "from pyharness.config import DEFAULTS" in src, \
        "应仅 import 静态 DEFAULTS 常量"
