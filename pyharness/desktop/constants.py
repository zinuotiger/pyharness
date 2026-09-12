"""Desktop shell constants."""
from __future__ import annotations

WINDOW_TITLE = "PyHarness"
WINDOW_WIDTH = 1280                       # F065 窗口尺寸(伪码锁定)
WINDOW_HEIGHT = 800
HOST = "127.0.0.1"                        # 仅回环(PRD F065 边界:禁绑非回环)
SSE_HEARTBEAT_S = 15.0                    # SSE 心跳注释行间隔(伪码 wait_for 超时)
BRIDGE_TIMEOUT_S = 60.0                   # js_api 跨线程调用超时(超时→本地 BUSY 错误体)
LISTEN_TIMEOUT_S = 15.0                   # uvicorn 就绪探测超时(失败退 1 不弹空窗)
CHANNEL = "desktop"                       # 审批裁决者通道名(by="desktop",伪码字面量)
WARN_RATIO = 0.8                          # 预算 warn 80% 阈值(PARAMETER-ANCHOR)

# 轨迹面板 6 类核心节点之外的全部合法 kind(含伪码 fallback 的 info,见偏离 11)
TIMELINE_KINDS: frozenset = frozenset({
    "user", "thought", "message", "tool", "guard", "approval",
    "budget", "recovery", "error", "info"})
# 疑似敏感键名(redact 命中即打码,INV-09 不泄密;与 acp._SENSITIVE_KEY_HINTS 同族)
_SENSITIVE_HINTS = ("secret", "token", "apikey", "api_key", "password",
                    "credential", "private", "key")
# HTTP 状态映射(ADR-011:4xx 客户端 / 5xx 引擎;与 spec 各函数异常表对齐)
_STATUS_FOR_CODE: dict[str, int] = {
    "EVT-100": 400, "EVT-101": 409, "EVT-102": 400, "EVT-104": 409,
    "EVT-105": 400, "EVT-106": 404,
    "CRED-701": 401,
    "APR-501": 403, "APR-503": 404, "GRD-403": 409,
    "QUE-001": 503, "PERS-202": 500,
}
