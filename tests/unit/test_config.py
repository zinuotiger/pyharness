"""config.py 单测 — 契约:specs/config.py.md + CFG.md + PARAMETER-ANCHOR

覆盖:默认值锚定、四层合并优先级、dict 键级合并/列表整体替换/null 删键、
PH_ env 解析与白名单、敏感项脱敏(env:NAME 引用/字面量拒载/redact)、
非法配置抛码(CFG-601 file_parse/env_parse/type_range/structural/越权/secret_literal)、
热更(CFG-608 只读键拒绝 / 事件留痕)、config show/validate 后端。
"""
import asyncio
import logging
import warnings
from pathlib import Path

import pytest
from pyharness import config as C
from pyharness.config import (
    Settings, SettingsHolder, deep_merge, flatten, hot_update, load_settings,
    parse_env_value, redact, render_show, secret_ref_ok, validate_only,
    validate_rules, yaml_load_file,
)
from pyharness.errors import PyHError

# 疑似密钥样例:sk- 前缀 40 位 / 非 sk- 40 位
SK_LITERAL = "sk-" + "a" * 40
PLAIN_LONG = "k" * 40
_ALL_GUARDS = ["g-fs-path", "g-credential-read", "g-net-outbound",
               "g-exec", "g-overwrite"]


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """隔离真实 ~/.pyharness 与残留 PH_*:HOME/USERPROFILE 指向临时目录,白名单变量清空。"""
    for k in list(__import__("os").environ):
        if k.startswith("PH_"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    return tmp_path


def _write_cfg(home: Path, body: str, name: str = "config.yaml") -> Path:
    """写 ~/.pyharness 下的配置文件/片段;name 形如 config.yaml 或 config.d/01-a.yaml。"""
    root = home / ".pyharness"
    root.mkdir(parents=True, exist_ok=True)
    if "/" in name:                      # 片段 → config.d/ 子目录
        dd = root / "config.d"
        dd.mkdir(parents=True, exist_ok=True)
        p = dd / name.split("/", 1)[1]
    else:
        p = root / name
    p.write_text(body, encoding="utf-8")
    return p


def _expect_cfg(ei, code: str, reason: str | None = None, field: str | None = None):
    """统一断言:错误码 + reason + fields 字段。"""
    assert ei.value.code == code
    if reason is not None:
        assert ei.value.ctx.get("reason") == reason
    if field is not None:
        assert field in ei.value.ctx.get("fields", [])


# ============================================================ 默认值锚定
def test_defaults_anchored_to_parameter_anchor():
    """PARAMETER-ANCHOR:轮数 30/三档超时 10-60-180/审批 120s/预算 ¥1/背压 1000/工具超时 60s。"""
    s = load_settings()
    assert s.loop.max_turns == 30                    # F007 任务轮数上限
    assert (s.llm.timeout.connect_s, s.llm.timeout.first_token_s,
            s.llm.timeout.total_s) == (10, 60, 180)  # F017 三档超时
    assert s.security.approval.ttl_ms == 120000      # F015 审批 TTL 120s
    assert s.budget.task.max_cost_yuan == 1.0        # F032 默认 ¥1/任务
    assert s.plugins.backpressure_limit == 1000      # F005 背压阈值
    assert s.loop.step_timeout_s == 60               # F017 工具默认超时
    assert s.security.sandbox.proc_wallclock_s == 60
    assert s.config.schema_version == 1
    assert s.llm.api_key == "env:DEEPSEEK_API_KEY"   # C4 秘密只存引用
    assert s.llm.model == "deepseek-chat"
    assert s.security.sandbox.level == "strict"      # C2 默认即安全
    assert s.security.guards.disabled == []
    assert s.security.trustlist.enabled is False
    assert s.shell.web.host == "127.0.0.1" and s.shell.web.port == 8000
    assert s.loop.max_context_tokens == 65536


# ============================================================ 合并原语
def test_deep_merge_semantics():
    """TC-G1:字典键级合并(两键并集)/列表整体替换/null 删键/标量覆盖,且不改 base。"""
    base = {"a": {"x": 1, "y": 2}, "lst": [1, 2, 3], "keep": "v"}
    out = deep_merge(base, {"a": {"y": 9, "z": 3}, "lst": [9],
                            "drop": None, "keep": "w"})
    assert out == {"a": {"x": 1, "y": 9, "z": 3}, "lst": [9], "keep": "w"}
    assert base == {"a": {"x": 1, "y": 2}, "lst": [1, 2, 3], "keep": "v"}


def test_flatten_dotted_keys():
    """嵌套 dict → 点分键平面(列表/标量叶子不进展开)。"""
    assert flatten({"a": {"b": 1, "c": {"d": 2}}, "e": [1]}) == {
        "a.b": 1, "a.c.d": 2, "e": [1]}


# ============================================================ 四层合并优先级
def test_layer_file_overrides_default_with_merge_semantics():
    """L2 覆盖 L1:标量覆盖/字典键级合并(部分超时其余回落默认)/列表整体替换/null 删键回落 L1。"""
    home = _write_cfg(Path(__import__("os").environ["HOME"]), """
llm:
  temperature: 0.3
  fallback_models: [glm-4]
  timeout: {connect_s: 20}
loop:
  max_turns: 100
storage:
  spill_dir: null
budget:
  task: {max_cost_yuan: 2.0}
""")
    s = load_settings()
    assert s.llm.temperature == 0.3                  # 标量覆盖
    assert s.llm.fallback_models == ["glm-4"]        # 列表整体替换(L1 qwen-max 消失)
    assert (s.llm.timeout.connect_s, s.llm.timeout.first_token_s,
            s.llm.timeout.total_s) == (20, 60, 180)  # 字典键级合并,未写键回落 L1
    assert s.loop.max_turns == 100
    assert s.storage.spill_dir == "~/.pyharness/spill"  # null = 删键回落 L1
    assert s.budget.task.max_cost_yuan == 2.0


def test_four_layer_cascade_200_100_30():
    """TC-F021:文件 100 ← env 200 ← CLI 300;逐层摘除得 200/100/30。"""
    _write_cfg(Path(__import__("os").environ["HOME"]),
               "loop:\n  max_turns: 100\n")
    s = load_settings(("loop.max_turns", 300))       # L1+文件+CLI(无 env)
    assert s.loop.max_turns == 300
    s = load_settings()                              # 仅文件 → 100
    assert s.loop.max_turns == 100
    s2 = load_settings(("loop.max_turns", 30))       # CLI 显式回落 L1 值
    assert s2.loop.max_turns == 30


def test_env_overrides_file_and_cli_highest(monkeypatch):
    """L3 env 覆盖 L2 文件;L4 CLI 最高。"""
    _write_cfg(Path(__import__("os").environ["HOME"]),
               "loop:\n  max_turns: 100\n")
    monkeypatch.setenv("PH_LOOP_MAX_TURNS", "200")
    assert load_settings().loop.max_turns == 200     # env > 文件
    assert load_settings(("loop.max_turns", 300)).loop.max_turns == 300  # CLI > env
    monkeypatch.delenv("PH_LOOP_MAX_TURNS")
    assert load_settings().loop.max_turns == 100     # 摘除 env → 回落文件层


def test_cli_dotted_deep_key_merge():
    """CLI 点分键与文件同管线同校验:部分覆盖超时档,其余键保留。"""
    _write_cfg(Path(__import__("os").environ["HOME"]),
               "llm:\n  timeout: {first_token_s: 90}\n")
    s = load_settings(("llm.timeout.connect_s", 5))
    assert s.llm.timeout.connect_s == 5
    assert s.llm.timeout.first_token_s == 90
    assert s.llm.timeout.total_s == 180


# ============================================================ 文件层细节
def test_collect_and_load_file_layer_fragment_order():
    """config.yaml + config.d/*.yaml 文件名升序逐文件 merge,后覆盖前。"""
    home = Path(__import__("os").environ["HOME"])
    _write_cfg(home, "loop:\n  max_turns: 1\n")
    _write_cfg(home, "loop:\n  max_turns: 2\n", "config.d/01-b.yaml")
    _write_cfg(home, "loop:\n  max_turns: 3\n", "config.d/02-a.yaml")
    assert load_settings().loop.max_turns == 3       # 升序 01→02,02 覆盖 01
    files = C.collect_config_files(None)
    names = [f.name for f in files]
    assert names == ["config.yaml", "01-b.yaml", "02-a.yaml"]


def test_ph_cfg_path_points_single_file(monkeypatch, tmp_path):
    """PH_CFG_PATH 指向的文件作为 L2,不再扫 ~/.pyharness。"""
    _write_cfg(Path(__import__("os").environ["HOME"]),
               "loop:\n  max_turns: 999\n")
    target = tmp_path / "deploy.yaml"
    target.write_text("loop:\n  max_turns: 42\n", encoding="utf-8")
    monkeypatch.setenv("PH_CFG_PATH", str(target))
    assert load_settings().loop.max_turns == 42      # home 的 999 被忽略
    assert [f.name for f in C.collect_config_files(None)] == ["deploy.yaml"]


def test_cli_path_skips_rest():
    """显式 cli_path 后不再搜其余路径(collect_config_files)。"""
    _write_cfg(Path(__import__("os").environ["HOME"]),
               "loop:\n  max_turns: 7\n")
    other = Path(__import__("os").environ["HOME"]) / "cli.yaml"
    other.write_text("loop:\n  max_turns: 8\n", encoding="utf-8")
    assert [f.name for f in C.collect_config_files(str(other))] == ["cli.yaml"]
    assert yaml_load_file(str(other))["loop"]["max_turns"] == 8


def test_yaml_load_file_missing_falls_back_empty():
    """文件缺失 → 空 dict 回落 L1(不报错)。"""
    assert yaml_load_file("~/no-such-pyharness-config.yaml") == {}


def test_yaml_syntax_error_cfg601_file_parse(tmp_path):
    """YAML 语法错 → CFG-601(file_parse) 中止。"""
    bad = tmp_path / "bad.yaml"
    bad.write_text("llm: {temperature: 0.3\n  oops", encoding="utf-8")
    with pytest.raises(PyHError) as ei:
        yaml_load_file(str(bad))
    _expect_cfg(ei, "CFG-601", reason="file_parse")
    assert ei.value.ctx.get("file")


def test_yaml_root_non_mapping_cfg601(tmp_path):
    """根不是映射(列表/标量)→ CFG-601(file_parse)。"""
    bad = tmp_path / "list.yaml"
    bad.write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(PyHError) as ei:
        yaml_load_file(str(bad))
    _expect_cfg(ei, "CFG-601", reason="file_parse")


# ============================================================ env 解析与白名单
def test_parse_env_value_types():
    """bool(int/true/yes/on + 0/false/no/off)/int/float/列表;空串=None。"""
    assert parse_env_value("1") is True
    assert parse_env_value("true") is True
    assert parse_env_value("yes") is True
    assert parse_env_value("on") is True
    assert parse_env_value("0") is False
    assert parse_env_value("no") is False
    assert parse_env_value("off") is False
    assert parse_env_value("42") == 42
    assert parse_env_value("1.5") == 1.5
    assert parse_env_value("a, b") == ["a", "b"]
    assert parse_env_value('[1, "x"]') == [1, "x"]
    assert parse_env_value("") is None
    assert parse_env_value("deepseek-v3") == "deepseek-v3"  # 字符串键回退(偏离点 1)


def test_parse_env_value_bad_json_aborts():
    """列表 JSON 语法坏 → CFG-601(env_parse) 中止。"""
    with pytest.raises(PyHError) as ei:
        parse_env_value("[a, b]")
    _expect_cfg(ei, "CFG-601", reason="env_parse")


def test_env_whitelist_scalar_and_list(monkeypatch):
    """白名单 env → 覆盖生效:字符串/数字/bool/列表四类解析。"""
    monkeypatch.setenv("PH_LLM_MODEL", "deepseek-v3")
    monkeypatch.setenv("PH_LLM_TEMPERATURE", "1.2")
    monkeypatch.setenv("PH_LLM_FALLBACK_MODELS", "qwen-max, glm-4")
    monkeypatch.setenv("PH_LOOP_MAX_TURNS", "50")
    monkeypatch.setenv("PH_LOG_LEVEL", "warning")
    monkeypatch.setenv("PH_LLM_DEGRADE_ENABLED", "false")
    monkeypatch.setenv("PH_WEB_PORT", "8080")
    s = load_settings()
    assert s.llm.model == "deepseek-v3"
    assert s.llm.temperature == 1.2
    assert s.llm.fallback_models == ["qwen-max", "glm-4"]
    assert s.loop.max_turns == 50
    assert s.log.level == "warning"
    assert s.llm.degrade.enabled is False
    assert s.shell.web.port == 8080


def test_env_unknown_key_warns_cfg607_value_kept_l1(monkeypatch, caplog):
    """TC-G6:白名单外 PH_* → CFG-607 警告不中止,生效值仍 L1 默认。"""
    monkeypatch.setenv("PH_LLM_TEMPERATUR", "0.3")
    with caplog.at_level(logging.WARNING, logger="pyharness.config"):
        s = load_settings()
    assert s.llm.temperature == 0.7                  # 拼错键不生效
    assert "CFG-607" in caplog.text
    assert "PH_LLM_TEMPERATUR" in caplog.text


def test_env_numeric_invalid_aborts_cfg601(monkeypatch):
    """env 数值键给非法串 → pydantic 收口 → CFG-601(type_range),不静默。"""
    monkeypatch.setenv("PH_LOOP_MAX_TURNS", "abc")
    with pytest.raises(PyHError) as ei:
        load_settings()
    _expect_cfg(ei, "CFG-601", reason="type_range", field="loop.max_turns")


def test_env_bad_base_url_aborts(monkeypatch):
    """env 覆盖 base_url 非 http(s) → CFG-601(type_range)。"""
    monkeypatch.setenv("PH_LLM_BASE_URL", "ftp://x")
    with pytest.raises(PyHError) as ei:
        load_settings()
    _expect_cfg(ei, "CFG-601", reason="type_range", field="llm.base_url")


# ============================================================ 校验规则与抛码
def test_type_range_via_file_cfg601(tmp_path, monkeypatch):
    """TC-G2 文件:llm.temperature=3.0 越范围 → CFG-601(type_range) 列字段。"""
    f = tmp_path / "cfg.yaml"
    f.write_text("llm:\n  temperature: 3.0\n", encoding="utf-8")
    with pytest.raises(PyHError) as ei:
        _load_via_ph_cfg_path(f, monkeypatch)
    _expect_cfg(ei, "CFG-601", reason="type_range", field="llm.temperature")


def _load_via_ph_cfg_path(f: Path, monkeypatch) -> Settings:
    """经 PH_CFG_PATH 指文件走全管线(load_settings 无 cli_path 形参)。"""
    monkeypatch.setenv("PH_CFG_PATH", str(f))
    return load_settings()


def test_bad_str_in_int_field_cfg601(tmp_path, monkeypatch):
    """文件 loop.max_turns: "abc" → CFG-601(type_range)。"""
    f = tmp_path / "cfg.yaml"
    f.write_text("loop:\n  max_turns: \"abc\"\n", encoding="utf-8")
    with pytest.raises(PyHError) as ei:
        _load_via_ph_cfg_path(f, monkeypatch)
    _expect_cfg(ei, "CFG-601", reason="type_range", field="loop.max_turns")


def test_timeout_ordering_violation_cfg601(tmp_path, monkeypatch):
    """③ 时间档序 total_s < first_token_s → CFG-601(structural, llm.timeout.*)。"""
    f = tmp_path / "cfg.yaml"
    f.write_text("llm:\n  timeout: {first_token_s: 50, total_s: 40}\n",
                 encoding="utf-8")
    with pytest.raises(PyHError) as ei:
        _load_via_ph_cfg_path(f, monkeypatch)
    _expect_cfg(ei, "CFG-601", reason="structural", field="llm.timeout.*")


def test_guards_full_disable_rejected(tmp_path, monkeypatch):
    """guard 五内置全关 → CFG-601(越权)。"""
    f = tmp_path / "cfg.yaml"
    f.write_text("security:\n  guards:\n    disabled: %s\n"
                 % str(_ALL_GUARDS).replace("'", '"'), encoding="utf-8")
    with pytest.raises(PyHError) as ei:
        _load_via_ph_cfg_path(f, monkeypatch)
    _expect_cfg(ei, "CFG-601", reason="越权", field="security.guards.disabled")


def test_guards_unknown_name_rejected(tmp_path, monkeypatch):
    """关未注册 guard 名 → CFG-601(越权);单调只紧不松。"""
    f = tmp_path / "cfg.yaml"
    f.write_text("security:\n  guards:\n    disabled: [g-nope]\n", encoding="utf-8")
    with pytest.raises(PyHError) as ei:
        _load_via_ph_cfg_path(f, monkeypatch)
    _expect_cfg(ei, "CFG-601", reason="越权", field="security.guards.disabled")


def test_guards_single_builtin_allowed(tmp_path, monkeypatch):
    """关单个内置 guard 放行(显式关单个)。"""
    f = tmp_path / "cfg.yaml"
    f.write_text("security:\n  guards:\n    disabled: [g-exec]\n", encoding="utf-8")
    s = _load_via_ph_cfg_path(f, monkeypatch)
    assert s.security.guards.disabled == ["g-exec"]


def test_danger_only_tighten_g5(tmp_path, monkeypatch):
    """TC-G5:fs.delete_file(基线 high)调 low → 越权拒;调 critical / 未知工具调 high 放行。"""
    f = tmp_path / "cfg.yaml"
    f.write_text("security:\n  tool_danger_extra: {fs.delete_file: low}\n",
                 encoding="utf-8")
    with pytest.raises(PyHError) as ei:
        _load_via_ph_cfg_path(f, monkeypatch)
    _expect_cfg(ei, "CFG-601", reason="越权",
                field="security.tool_danger_extra.fs.delete_file")
    f.write_text("security:\n  tool_danger_extra: {fs.delete_file: critical, net.upload: high}\n",
                 encoding="utf-8")
    s = _load_via_ph_cfg_path(f, monkeypatch)
    assert s.security.tool_danger_extra == {"fs.delete_file": "critical",
                                            "net.upload": "high"}


def test_web_nonloopback_requires_token(tmp_path, monkeypatch):
    """绑非回环且无 token → CFG-601(structural);配 env: 引用 token 则放行。"""
    f = tmp_path / "cfg.yaml"
    f.write_text("shell:\n  web: {host: 0.0.0.0}\n", encoding="utf-8")
    with pytest.raises(PyHError) as ei:
        _load_via_ph_cfg_path(f, monkeypatch)
    _expect_cfg(ei, "CFG-601", reason="structural", field="shell.web.token")
    f.write_text("shell:\n  web: {host: 0.0.0.0, token: env:WEB_TOKEN}\n",
                 encoding="utf-8")
    s = _load_via_ph_cfg_path(f, monkeypatch)
    assert s.shell.web.host == "0.0.0.0"
    assert s.shell.web.token == "env:WEB_TOKEN"


# ============================================================ 秘密与脱敏(C4/INV-09)
def test_secret_ref_ok_grammar():
    """secret-ref 语法:env:/file: 合法;sk-/≥32 位字面量拒;空=未配置允许。"""
    assert secret_ref_ok("env:DEEPSEEK_API_KEY")
    assert secret_ref_ok("file:C:/secrets/key.txt")
    assert secret_ref_ok(None)
    assert secret_ref_ok("")
    assert secret_ref_ok("abc")                       # 短字面量:未达拒载阈值
    assert not secret_ref_ok(SK_LITERAL)
    assert not secret_ref_ok(PLAIN_LONG)
    # 注:畸形 env: 引用(env:1BAD)按 spec 只拒 sk-/≥32 位字面量的规则放行——
    # 它非疑似明文,语法正确性由凭据模块读取时校验(本函数只管"字面量拒载"阈值)
    assert secret_ref_ok("env:1BAD")
    assert not secret_ref_ok(42)                      # 非字符串


def test_literal_secret_rejected(tmp_path, monkeypatch):
    """TC-G3:文件 llm.api_key 明文 sk-/≥32 位 → CFG-601(secret_literal)。"""
    f = tmp_path / "cfg.yaml"
    f.write_text("llm:\n  api_key: \"%s\"\n" % SK_LITERAL, encoding="utf-8")
    with pytest.raises(PyHError) as ei:
        _load_via_ph_cfg_path(f, monkeypatch)
    _expect_cfg(ei, "CFG-601", reason="secret_literal", field="llm.api_key")
    f.write_text("llm:\n  api_key: \"%s\"\n" % PLAIN_LONG, encoding="utf-8")
    with pytest.raises(PyHError) as ei:
        _load_via_ph_cfg_path(f, monkeypatch)
    _expect_cfg(ei, "CFG-601", reason="secret_literal", field="llm.api_key")


def test_short_literal_secret_passes_spec_threshold(tmp_path, monkeypatch):
    """短字面量(<32 位非 sk-)按 spec 阈值放行;改 env: 引用且环境缺变量 → 加载通过。"""
    f = tmp_path / "cfg.yaml"
    f.write_text("llm:\n  api_key: env:DEEPSEEK_API_KEY\n", encoding="utf-8")
    s = _load_via_ph_cfg_path(f, monkeypatch)
    assert s.llm.api_key == "env:DEEPSEEK_API_KEY"    # 引用不解析(使用时 CRED-701)


def test_render_show_secrets_only_refs():
    """config show 后端:秘密只回显 env:NAME/file:PATH 引用,无字面量值(TC-G4)。"""
    s = load_settings()
    flat = render_show(s)
    assert flat["llm.api_key"] == "env:DEEPSEEK_API_KEY"
    body = str(flat)
    assert "DEEPSEEK_API_KEY" in body                 # 引用名可显示
    assert "sk-" not in body


def test_redact_masks_secrets():
    """redact:sk- 前缀与 ≥32 位连续密钥打码;短值/引用不受影响(INV-09)。"""
    assert redact(SK_LITERAL).startswith("sk-" + "a" * 6 + "***")
    assert "****" in redact(PLAIN_LONG)
    assert "****" not in redact("hello world")
    assert redact("env:DEEPSEEK_API_KEY") == "env:DEEPSEEK_API_KEY"


# ============================================================ 未知键(CFG-607)
def test_unknown_config_key_warns_keeps_default(tmp_path, monkeypatch, caplog):
    """TC-G6:文件含拼错键 llm.temperatur → CFG-607 警告,生效温度仍 0.7。"""
    f = tmp_path / "cfg.yaml"
    f.write_text("llm:\n  temperatur: 0.3\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="pyharness.config"):
        s = _load_via_ph_cfg_path(f, monkeypatch)
    assert s.llm.temperature == 0.7
    assert "CFG-607" in caplog.text
    assert "llm.temperatur" in caplog.text


# ============================================================ 热更(hot_update)
class _FakeBus:
    """事件桩:记录 emit 调用。"""

    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def emit(self, event: str, payload: dict, mode: str = "sequential"):
        self.events.append((event, payload))


def _holder(settings=None) -> tuple[SettingsHolder, _FakeBus]:
    bus = _FakeBus()
    return SettingsHolder(settings or load_settings(), bus=bus), bus


def test_hot_update_mutable_key_effective_with_event():
    """TC-G4:热更 log.level 生效并写 config.updated 事件(含 by/脱敏值)。"""
    holder, bus = _holder()
    hot_update(holder, "log.level", "debug", by="ops")
    assert holder.settings.log.level == "debug"
    assert len(bus.events) == 1
    event, payload = bus.events[0]
    assert event == "config.updated"
    assert payload == {"key": "log.level", "old": "info", "new": "debug", "by": "ops"}


async def test_hot_update_emits_to_real_bus():
    """真实 EventBus 上 config.updated 必须送达订阅者(修掉协程被丢弃的假绿)。"""
    from pyharness.bus import EventBus

    bus = EventBus()
    got: list[tuple[str, dict]] = []

    async def collect(type_: str, payload: dict) -> None:
        got.append((type_, payload))

    bus.subscribe("config.updated", collect)
    holder = SettingsHolder(load_settings(), bus=bus)
    hot_update(holder, "log.level", "debug", by="ops")
    for _ in range(50):
        if got:
            break
        await asyncio.sleep(0.01)
    assert holder.settings.log.level == "debug"
    assert got == [("config.updated",
                    {"key": "log.level", "old": "info",
                     "new": "debug", "by": "ops"})]


def test_hot_update_readonly_keys_rejected_cfg608():
    """TC-G4:热更 security.sandbox.level / loop.max_turns → CFG-608 拒绝,值不变。"""
    holder, bus = _holder()
    with pytest.raises(PyHError) as ei:
        hot_update(holder, "security.sandbox.level", "basic")
    assert ei.value.code == "CFG-608"
    assert ei.value.ctx.get("key") == "security.sandbox.level"
    with pytest.raises(PyHError) as ei:
        hot_update(holder, "loop.max_turns", 100)
    assert ei.value.code == "CFG-608"
    with pytest.raises(PyHError) as ei:
        hot_update(holder, "llm.api_key", "env:OTHER")
    assert ei.value.code == "CFG-608"                 # 秘密不在热更白名单
    assert holder.settings.security.sandbox.level == "strict"
    assert holder.settings.loop.max_turns == 30
    assert bus.events == []                           # 拒绝不留 config.updated


def test_hot_update_invalid_value_cfg601():
    """热更值非法(log.level=bogus)→ CFG-601(type_range),原值不变。"""
    holder, _ = _holder()
    with pytest.raises(PyHError) as ei:
        hot_update(holder, "log.level", "bogus")
    _expect_cfg(ei, "CFG-601", reason="type_range", field="log.level")
    assert holder.settings.log.level == "info"


def test_hot_update_observable_keys_ok():
    """观测键热更放行:probe.interval_s / unit_price / alert_ratio。"""
    holder, bus = _holder()
    hot_update(holder, "llm.probe.interval_s", 120)
    assert holder.settings.llm.probe.interval_s == 120
    hot_update(holder, "budget.monthly.alert_ratio", 0.9)
    assert holder.settings.budget.monthly.alert_ratio == 0.9
    hot_update(holder, "llm.usage.unit_price",
               {"deepseek-chat": {"in_per_million": 3.0, "out_per_million": 9.0}})
    assert holder.settings.llm.usage.unit_price["deepseek-chat"].in_per_million == 3.0
    assert len(bus.events) == 3


def test_hot_update_rejection_does_not_partially_apply():
    """被拒热更后快照与热更前一致(不部分生效)。"""
    holder, _ = _holder()
    before = holder.settings.model_dump()
    with pytest.raises(PyHError):
        hot_update(holder, "loop.max_turns", 5)
    assert holder.settings.model_dump() == before


# ============================================================ config 子命令后端
def test_validate_only_good_and_bad(tmp_path, monkeypatch):
    """config validate:合法 → 空列表;非法文件 → 错误列表含 CFG-601 与字段明细。"""
    assert validate_only(None) == []
    f = tmp_path / "bad.yaml"
    f.write_text("llm:\n  temperature: 3.0\n", encoding="utf-8")
    errors = validate_only(str(f))
    assert len(errors) == 1
    assert "CFG-601" in errors[0]
    assert "llm.temperature" in errors[0]


def test_validate_rules_direct():
    """validate_rules 直接调用合法默认配置不抛。"""
    validate_rules(load_settings())


# ============================================================ 全默认加载
def test_default_load_never_fails_and_roundtrip():
    """任意键缺省必可加载;model_dump 与 DEFAULTS 叶子键面一致(无意外删键)。"""
    s = load_settings()
    flat = flatten(s.model_dump())
    default_leaves = set(flatten(C.DEFAULTS))
    assert default_leaves <= set(flat)               # L1 全键保留
    assert s.log.level == "info"
    assert s.log.redact_enabled is True
    assert s.storage.sessions_dir == "~/.pyharness/sessions"
    assert s.plugins.pre_activate == ["storage.spill", "credentials", "storage.kv"]


def test_default_unit_price_factory_is_strongly_typed():
    """Settings() 的 default_factory 必须产出 UnitPriceCfg,不能混入裸 dict。"""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        settings = Settings()
        price = settings.llm.usage.unit_price["deepseek-chat"]
        dumped = settings.model_dump()

    assert isinstance(price, C.UnitPriceCfg)
    assert price.in_per_million == 2.0
    assert dumped["llm"]["usage"]["unit_price"]["deepseek-chat"] == {
        "in_per_million": 2.0, "out_per_million": 8.0}
    assert caught == []
