"""Platform templates: complete migrated implementation."""
from __future__ import annotations
from pyharness.application.platform_models import AgentDefinition


TEMPLATES = [
    AgentDefinition(name='通用助手', description='使用已配置模型；默认禁止代码执行').model_dump(),
    AgentDefinition(name='受治理代码变更 Agent', description='在私有沙盒中修复示例、测试并生成 Patch',
        system_prompt='先给出简短公开计划。读取文件，修复明确 Bug，运行测试；失败最多修复重试一次。只能操作私有工作区。最终总结变更与验证，不披露私有推理。',
        permission_policy='standard', allowed_tools=['fs.read_file','fs.list_dir','fs.write_file','exec.python_run','exec.shell_run'],
        sandbox_profile={'mode':'isolated'}).model_dump(),
    AgentDefinition(name='知识问答 Agent', description='关键词检索、明确引用，无证据时拒答',
        require_evidence=True, strict_knowledge=True,
        system_prompt='只依据提供的检索证据回答，给出来源和行号。无可靠证据时明确拒绝回答。').model_dump(),
]
