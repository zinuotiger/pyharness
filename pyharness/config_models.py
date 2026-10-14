"""Config models: complete migrated implementation."""
from __future__ import annotations
from typing import Literal, Optional
from pydantic import BaseModel, Field, field_validator


class UnitPriceCfg(BaseModel):
    """单价条目(元/百万 token,F029/N14):只影响估算/报表。"""
    in_per_million: float = Field(..., ge=0)
    out_per_million: float = Field(..., ge=0)

def _default_unit_price() -> dict[str, UnitPriceCfg]:
    return {
        "deepseek-chat": UnitPriceCfg(in_per_million=2.0, out_per_million=8.0),
        "qwen-max": UnitPriceCfg(in_per_million=4.0, out_per_million=12.0),
    }

class TimeoutCfg(BaseModel):
    """llm.timeout — 三档超时(F017);档序在 validate_rules ③ 复核。"""
    connect_s: int = Field(10, ge=1, le=300)
    first_token_s: int = Field(60, ge=1, le=600)
    total_s: int = Field(180, ge=1, le=1800)

class RetryCfg(BaseModel):
    """llm.retry — 退避(F028)。"""
    attempts: int = Field(4, ge=0, le=8)
    base_delay_s: float = Field(1.0, ge=0.1, le=60)
    jitter: float = Field(0.3, ge=0, le=0.5)

class DegradeCfg(BaseModel):
    """llm.degrade — 降级链(F013)。"""
    enabled: bool = True
    rate_limit_consecutive: int = Field(2, ge=1, le=5)
    max_per_session: int = Field(5, ge=1, le=20)

class ProbeCfg(BaseModel):
    """llm.probe — 探针周期(F033)。"""
    interval_s: int = Field(60, ge=10, le=3600)

class UsageCfg(BaseModel):
    """llm.usage — 单价表(只影响估算/报表)。"""
    unit_price: dict[str, UnitPriceCfg] = Field(default_factory=_default_unit_price)

class LlmCfg(BaseModel):
    """llm.* — 模型域(F012/F013/F028/F029/F030/F033)。"""
    model: str = "deepseek-chat"
    base_url: str = "https://api.deepseek.com"
    api_key: str = "env:DEEPSEEK_API_KEY"       # secret-ref 型,字面量由 validate_rules 拒载
    temperature: float = Field(0.7, ge=0, le=1.5)
    max_tokens: int = Field(4096, ge=1, le=32768)
    fallback_models: list[str] = Field(default_factory=lambda: ["qwen-max"])
    timeout: TimeoutCfg = Field(default_factory=TimeoutCfg)
    retry: RetryCfg = Field(default_factory=RetryCfg)
    degrade: DegradeCfg = Field(default_factory=DegradeCfg)
    probe: ProbeCfg = Field(default_factory=ProbeCfg)
    usage: UsageCfg = Field(default_factory=UsageCfg)

    @field_validator("base_url")
    @classmethod
    def _url_ok(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError("base_url 须 http(s):// 开头(合法 URL)")
        return v

class CompactCfg(BaseModel):
    """loop.compact — 上下文压缩(F058)。"""
    trigger_ratio: float = Field(0.75, ge=0.5, le=0.95)
    min_new_rounds: int = Field(10, ge=1, le=100)
    keep_recent_rounds: int = Field(12, ge=1, le=100)
    summarize_budget_tokens: int = Field(400, ge=50, le=2000)

class ContentCfg(BaseModel):
    """loop.content — 工具内容截断(F034/F037/F038)。"""
    file_spill_bytes: int = Field(65536, ge=1024, le=1048576)
    fetch_max_chars: int = Field(32768, ge=1024, le=1048576)
    search_result_chars: int = Field(8000, ge=512, le=32768)

class NetworkCfg(BaseModel):
    """security.network — 外发 allowlist(F023/N13/F037)。"""
    allowed_domains: list[str] = Field(default_factory=list)
    web_search_per_session: int = Field(20, ge=0, le=1000)
    search_backend: Literal["disabled", "bing", "duckduckgo"] = "bing"
    search_endpoint: str = "https://cn.bing.com/search"

class PolicyCfg(BaseModel):
    """security.policy — 权限预设基集上只增的 deny/只读例外(F023/F055)。"""
    deny_tools_extra: list[str] = Field(default_factory=list)
    read_extra_dirs: list[str] = Field(default_factory=list)
    preset: Literal["locked", "readonly", "standard", "strict"] = "strict"

class GuardsCfg(BaseModel):
    """security.guards — 五内置 guard 显式关单个(禁全关/禁未注册名,validate_rules 复核)。"""
    disabled: list[str] = Field(default_factory=list)

class ApprovalCfg(BaseModel):
    """security.approval — 审批 TTL/合并窗(F015)。"""
    ttl_ms: int = Field(120000, ge=1000, le=3600000)   # 120s,超时=denied 安全默认
    merge_window_s: int = Field(60, ge=0, le=600)

class TrustlistCfg(BaseModel):
    """security.trustlist — 信任名单默认关(SEC §5)。"""
    enabled: bool = False

class AttachmentCfg(BaseModel):
    """security.attachment — 附件限制(F061)。"""
    max_bytes: int = Field(10485760, ge=1, le=104857600)
    max_per_message: int = Field(5, ge=1, le=20)
    mime_whitelist: list[str] = Field(default_factory=lambda: [
        "image/jpeg", "image/png", "image/webp", "image/gif"])

class BudgetTaskCfg(BaseModel):
    """budget.task — 单任务硬闸(F032/N5)。"""
    max_in_tokens: int = Field(2_000_000, ge=1, le=100_000_000)
    max_out_tokens: int = Field(50_000, ge=1, le=10_000_000)
    max_cost_yuan: float = Field(1.0, ge=0.01, le=1_000_000)

class MonthlyCfg(BaseModel):
    """budget.monthly — 月度成本估算(跨会话按日志聚合)。"""
    limit_yuan: float = Field(0.0, ge=0)               # 0=不启用;>0=元
    alert_ratio: float = Field(0.8, ge=0.5, le=0.99)

class BudgetCfg(BaseModel):
    """budget.* — 成本域(F032/N5/F029)。"""
    task: BudgetTaskCfg = Field(default_factory=BudgetTaskCfg)
    warn_ratio: float = Field(0.8, ge=0.5, le=0.99)
    subagent_ratio: float = Field(0.25, ge=0.01, le=1)
    monthly: MonthlyCfg = Field(default_factory=MonthlyCfg)

class LogJsonlCfg(BaseModel):
    """log.jsonl — 事件日志攒批(PRD §3.6)。"""
    flush_interval_s: float = Field(0.5, ge=0.05, le=10)
    flush_batch: int = Field(64, ge=1, le=1024)

class LogCfg(BaseModel):
    """log.* — 日志域(F009/F011);事件 JSONL 不受 log.level 影响。"""
    level: Literal["debug", "info", "warning", "error"] = "info"
    file: str = ""                                       # 空 = 仅控制台
    rotate_bytes: int = Field(52_428_800, ge=1_048_576, le=1_073_741_824)
    redact_enabled: bool = True                          # 全出口脱敏(INV-09)
    jsonl: LogJsonlCfg = Field(default_factory=LogJsonlCfg)

class StorageJsonlCfg(BaseModel):
    """storage.jsonl — 会话日志轮转(F011/N11)。"""
    rotate_bytes: int = Field(52_428_800, ge=1_048_576, le=1_073_741_824)

class SpillCfg(BaseModel):
    """storage.spill — spill 私有区限额(F039/N3)。"""
    max_per_file_bytes: int = Field(10_485_760, ge=1024, le=104_857_600)
    max_per_session_mb: int = Field(100, ge=1, le=10240)

class FtsCfg(BaseModel):
    """storage.fts — 会话 FTS 索引(F057)。"""
    index_batch_ms: int = Field(200, ge=10, le=5000)
    query_timeout_s: int = Field(5, ge=1, le=60)
    result_limit: int = Field(20, ge=1, le=100)

class StorageCfg(BaseModel):
    """storage.* — 存储域(F011/F039/F055/F056/F057);~ 路径运行时再展开。"""
    root: str = "~/.pyharness"
    sessions_dir: str = "~/.pyharness/sessions"
    db_path: str = "~/.pyharness/pyharness.db"
    workspaces_dir: str = "~/.pyharness/workspaces"
    archive_days: int = Field(30, ge=0, le=3650)
    jsonl: StorageJsonlCfg = Field(default_factory=StorageJsonlCfg)
    spill_dir: str = "~/.pyharness/spill"
    spill: SpillCfg = Field(default_factory=SpillCfg)
    fts: FtsCfg = Field(default_factory=FtsCfg)

class McpServerCfg(BaseModel):
    """MCP stdio server 配置;command 为 argv,不经过 shell。"""
    name: str = Field(min_length=1, max_length=64)
    command: list[str] = Field(min_length=1, max_length=64)
    enabled: bool = True
    timeout_s: int = Field(30, ge=1, le=600)
    # None preserves existing registrations; [] exposes no tools. Local policy only.
    allowed_tools: Optional[list[str]] = None

class SkillsCfg(BaseModel):
    """skills.* — local skill root and optional remote registry."""
    dir: str = "~/.pyharness/skills"
    registry_url: str = ""
    max_package_bytes: int = Field(5242880, ge=1024, le=104857600)
    max_files: int = Field(200, ge=1, le=10000)

class ShellWebCfg(BaseModel):
    """shell.web — Web 外壳(F065);默认仅回环,非回环须配 token(validate_rules)。"""
    host: str = "127.0.0.1"
    port: int = Field(8000, ge=1, le=65535)
    token: str = ""                                        # secret-ref 型

class ShellAcpCfg(BaseModel):
    """shell.acp — ACP 桥(F066)。"""
    enabled: bool = False

class ShellCfg(BaseModel):
    """shell.* — 外壳域(F064-F066)。"""
    web: ShellWebCfg = Field(default_factory=ShellWebCfg)
    acp: ShellAcpCfg = Field(default_factory=ShellAcpCfg)

# Preserve historical import and pickle identities through the compatibility facade.
UnitPriceCfg.__module__ = 'pyharness.config'
TimeoutCfg.__module__ = 'pyharness.config'
RetryCfg.__module__ = 'pyharness.config'
DegradeCfg.__module__ = 'pyharness.config'
ProbeCfg.__module__ = 'pyharness.config'
UsageCfg.__module__ = 'pyharness.config'
LlmCfg.__module__ = 'pyharness.config'
CompactCfg.__module__ = 'pyharness.config'
ContentCfg.__module__ = 'pyharness.config'
NetworkCfg.__module__ = 'pyharness.config'
PolicyCfg.__module__ = 'pyharness.config'
GuardsCfg.__module__ = 'pyharness.config'
ApprovalCfg.__module__ = 'pyharness.config'
TrustlistCfg.__module__ = 'pyharness.config'
AttachmentCfg.__module__ = 'pyharness.config'
BudgetTaskCfg.__module__ = 'pyharness.config'
MonthlyCfg.__module__ = 'pyharness.config'
BudgetCfg.__module__ = 'pyharness.config'
LogJsonlCfg.__module__ = 'pyharness.config'
LogCfg.__module__ = 'pyharness.config'
StorageJsonlCfg.__module__ = 'pyharness.config'
SpillCfg.__module__ = 'pyharness.config'
FtsCfg.__module__ = 'pyharness.config'
StorageCfg.__module__ = 'pyharness.config'
McpServerCfg.__module__ = 'pyharness.config'
SkillsCfg.__module__ = 'pyharness.config'
ShellWebCfg.__module__ = 'pyharness.config'
ShellAcpCfg.__module__ = 'pyharness.config'
ShellCfg.__module__ = 'pyharness.config'
