"""tests/e2e/ — 端到端目录（夹具见 ``tests/conftest.py``）。

E2E 口径：装配**真实**引擎（真 store / 真 SessionLog / 真 GuardChain / 真
ToolExecutor / 真持久化），仅 LLM 适配器为脚本替身（无网络环境唯一可替换点，
且不在被测链路上）。夹具 ``e2e_factory`` / ``adapter_factory`` 定义在
``tests/conftest.py``，与 ``tests/acceptance`` 共用同一套真实装配原语。
"""
