"""DesktopRoutes operations, separated from facade assembly."""
from __future__ import annotations
from typing import TYPE_CHECKING
from fastapi import Depends
from pyharness.errors import PyHError
if TYPE_CHECKING:  # 仅注解引用(app↔bridge 环形依赖,运行时不需要)
    pass

class DesktopRoutes:
    """DesktopRoutes responsibility; state is owned by the composing facade."""

    def mount_api(self) -> None:
        """注册全部端点(只读投影 + 引擎门面写 + SSE)+ PyHError 统一错误体(ADR-011)。"""
        a = self.api
        a.add_api_route("/", self._index_page, methods=["GET"])
        a.add_api_route("/classic", self._index_page, methods=["GET"])
        a.add_api_route('/assets/{asset_path:path}', self._appearance_asset, methods=['GET'])
        a.add_api_route("/api/sessions", self.create_session, methods=["POST"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/sessions", self.list_sessions, methods=["GET"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/sessions/{sid}", self.delete_session,
                        methods=["DELETE"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/sessions/{sid}/messages", self.session_messages,
                        methods=["GET"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/sessions/{sid}/messages", self.create_message,
                        methods=["POST"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/sessions/{sid}/timeline", self.session_timeline,
                        methods=["GET"],                            # 轨迹(核心)
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/sessions/{sid}/event/{seq}", self.event_detail,
                        methods=["GET"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/sessions/{sid}/telemetry", self.telemetry_report,
                        methods=["GET"],            # F066 审计导出(桌面面)
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/sessions/{sid}/governance-audit",
                        self.governance_audit,
                        methods=["GET"],            # GAP-7 治理因果审计面
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/sessions/{sid}/governance-evidence",
                        self.governance_evidence,
                        methods=["GET"],            # GAP-8 证据归档面
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/stream", self.stream_sse, methods=["GET"],
                        dependencies=[Depends(self._require_api_auth)])  # SSE
        a.add_api_route("/api/approvals/pending", self.pending_approvals,
                        methods=["GET"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/approvals/{sid}/{aid}", self.decide_approval_for,
                        methods=["POST"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/approvals/{aid}", self.decide_approval,
                        methods=["POST"],
                        dependencies=[Depends(self._require_api_auth)])  # 审批弹窗裁决
        a.add_api_route("/api/asks/pending", self.pending_asks,
                        methods=["GET"],                              # #38 反问轮询
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/asks/{sid}/{ask_id}", self.answer_ask_for,
                        methods=["POST"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/asks/{ask_id}", self.answer_ask,
                        methods=["POST"],
                        dependencies=[Depends(self._require_api_auth)])  # #38 反问答复
        a.add_api_route("/api/plugins", self.list_plugins,
                        methods=["GET"],                              # 插件列表
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/plugins/load", self.plugin_load,
                        methods=["POST"],
                        dependencies=[Depends(self._require_api_auth)])  # 插件装载
        a.add_api_route("/api/plugins/unload", self.plugin_unload,
                        methods=["POST"],
                        dependencies=[Depends(self._require_api_auth)])  # 插件卸载
        a.add_api_route("/api/plugins/reload", self.plugin_reload,
                        methods=["POST"],
                        dependencies=[Depends(self._require_api_auth)])  # 插件热重载
        a.add_api_route("/api/preset", self.set_preset,
                        methods=["POST"],
                        dependencies=[Depends(self._require_api_auth)])  # 权限档位切换
        a.add_api_route("/api/capabilities", self.capabilities,
                        methods=["GET"],                              # 双壳能力契约
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/tenant", self.tenant_state, methods=["GET"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/settings/models", self.list_model_profiles,
                        methods=["GET"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/settings/models", self.save_model_profile,
                        methods=["POST"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/settings/models/{profile_id}/activate",
                        self.activate_model_profile, methods=["POST"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/settings/models/{profile_id}",
                        self.delete_model_profile, methods=["DELETE"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/skills", self.list_skills,
                        methods=["GET"],                              # 技能目录
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/skills/registry/search", self.search_skills,
                        methods=["GET"],                              # Registry 搜索
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/skills/reload", self.reload_skills,
                        methods=["POST"],
                        dependencies=[Depends(self._require_api_auth)])  # 技能库重扫
        a.add_api_route("/api/sessions/{sid}/skills/install",
                        self.install_skill, methods=["POST"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/sessions/{sid}/skills/{name}/rollback",
                        self.rollback_skill, methods=["POST"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/sessions/{sid}/skills/{name}/remove",
                        self.remove_skill, methods=["POST"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/skills/{name}/versions", self.skill_versions,
                        methods=["GET"],                              # 已装版本
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/skills/{name}", self.skill_detail,
                        methods=["GET"],                              # 技能正文
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/budget/{sid}", self.budget_dashboard, methods=["GET"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/attachments", self.upload_attachment, methods=["POST"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/sessions/{sid}/workflow", self.run_workflow,
                        methods=["POST"],
                        dependencies=[Depends(self._require_api_auth)])
        # ------------------------------------------------------------ 编排管理面
        a.add_api_route("/api/sessions/{sid}/jobs", self.list_jobs,
                        methods=["GET"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/sessions/{sid}/jobs", self.start_job,
                        methods=["POST"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/sessions/{sid}/jobs/{job_id}", self.job_status,
                        methods=["GET"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/sessions/{sid}/jobs/{job_id}/cancel",
                        self.cancel_job, methods=["POST"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/sessions/{sid}/schedules", self.list_schedules,
                        methods=["GET"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/sessions/{sid}/schedules/action",
                        self.schedule_action, methods=["POST"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/sessions/{sid}/subagents", self.list_subagents,
                        methods=["GET"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/sessions/{sid}/subagents", self.spawn_subagent,
                        methods=["POST"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/sessions/{sid}/subagents/{sub_id}/cancel",
                        self.cancel_subagent, methods=["POST"],
                        dependencies=[Depends(self._require_api_auth)])
        # F063 编辑/重发 + #12 反馈 + #57 webhook(桌面后端面;前端按钮在 ui/)
        # 固定后缀必须先于 {seq} 注册,否则 "last-user" 会被当成 int seq。
        a.add_api_route("/api/sessions/{sid}/messages/last-user",
                        self.edit_last_user, methods=["PATCH"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/sessions/{sid}/messages/last-user/resend",
                        self.resend_last_user, methods=["POST"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/sessions/{sid}/messages/last-agent/feedback",
                        self.feedback_last_agent, methods=["POST"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/sessions/{sid}/messages/{seq}",
                        self.edit_message, methods=["PATCH"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/sessions/{sid}/messages/{seq}/resend",
                        self.resend_message, methods=["POST"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/sessions/{sid}/messages/{seq}/feedback",
                        self.feedback, methods=["POST"],
                        dependencies=[Depends(self._require_api_auth)])
        a.add_api_route("/api/webhook", self.webhook_ingest, methods=["POST"])
        a.add_exception_handler(PyHError, self._pyhe_handler)
        a.add_exception_handler(Exception, self._unexpected_handler)

