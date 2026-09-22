"""tests/acceptance/ — 验收层：面向**系统行为**的端到端能力验收。

与 ``tests/e2e`` 的分工：

- ``tests/e2e``        —— 真实引擎装配 + 生产式调用路径（链路是否走得通）；
- ``tests/acceptance`` —— 从**外壳/服务入口**验收能力（用户能用到什么）。

本目录一律走真实装配（夹具见 ``tests/conftest.py``），不得把 unit 测试搬进来
冒充验收。
"""
