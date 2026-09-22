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


def native_event_owner(tenant_id: str) -> str:
    """原生壳事件订阅属主(**按租户唯一**)。

    2026-09-21 R8:`DesktopNativeController` 每个实例服务一个租户,却用**固定**
    ``"desktop-native"`` 做订阅属主,而 ``close()`` 按属主摘除 ⇒ 多租户(或同进程
    重开)时**关闭一个控制器会摘掉另一个租户的订阅**(其窗口从此收不到事件)。
    同类缺陷第三次出现(engine 订阅属主 / 插件 guard 钩子 / 此处),故抽成单点并
    由静态不变量锁死:属主串必须含租户。
    """
    return f"desktop-native:{tenant_id or 'default'}"

# 轨迹面板 6 类核心节点之外的全部合法 kind(含伪码 fallback 的 info,见偏离 11)
TIMELINE_KINDS: frozenset = frozenset({
    "user", "thought", "message", "tool", "guard", "approval",
    "budget", "recovery", "error", "info"})
# 疑似敏感键名(redact 命中即打码,INV-09 不泄密;与 acp._SENSITIVE_KEY_HINTS 同族)
_SENSITIVE_HINTS = ("secret", "token", "apikey", "api_key", "password",
                    "credential", "private", "key")
# HTTP 状态映射(ADR-011:4xx 客户端 / 5xx 引擎;与 spec 各函数异常表对齐)
# 2026-09-21 扩:此前只映射 12 码,**客户端成因**的码(GRD-401/SKL-901/BUS-002/003/
# CFG-60x/JOB-001/TLB-802/803)未映射 ⇒ 统一回落 500("引擎内部错误"),与 ADR-011
# 的划分相反:客户端拿到 5xx 会当服务端故障重试/告警,而实际是自己请求的问题。
_STATUS_FOR_CODE: dict[str, int] = {
    "EVT-100": 400, "EVT-101": 409, "EVT-102": 400, "EVT-104": 409,
    "EVT-105": 400, "EVT-106": 404,
    "CRED-701": 401,
    "APR-501": 403, "APR-503": 404, "GRD-403": 409,
    "QUE-001": 503, "PERS-202": 500,
    # ---- 客户端成因(4xx/429/503):被拒调用 / 未找到 / 非法迁移 / 非法输入 ----
    "GRD-401": 403,          # 调用被拒(guard 单调拒绝)
    "APR-502": 409,          # 审批等待被取消
    "SKL-901": 404,          # 技能未找到
    "BUS-002": 409,          # 保留名/重名注册冲突
    "BUS-003": 409,          # 插件状态机非法迁移
    "CFG-601": 400,          # 提交的配置非法/越权
    "CFG-607": 400,          # 未知配置键/白名单外 env
    "CFG-608": 409,          # 热更只读键被拒
    "JOB-001": 503,          # 并发 job 达上限拒新(与 QUE-001 同类:资源上限)
    "TLB-802": 404,          # 工具未注册/目标不存在
    "TLB-803": 400,          # 参数校验失败
    # TLB-805(执行超时/Provider 异常)/CYC-999/PERS-*/CRED-703 属**引擎侧**,留 500
}
