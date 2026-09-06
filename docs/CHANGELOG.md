# CHANGELOG — PyHarness 文档版本记录

> 规则: MAJOR/MINOR 文档更新时登记;同一会话内小修(错别字/单点修复)不逐条记,由 IMPACT-MATRIX 记录
> 格式: 日期 | 版本 | 变更 | 涉及文件

## 2026-09-06 — v1.0(首个完整交付)

| 时间 | 变更 | 涉及文件 |
|------|------|---------|
| 上午 | full-pipeline 启动:需求文档 + 架构设计(精简版草案) | 需求文档.md / 架构设计.md |
| 下午 | 用户拍板全功能(66 项)+ 走完整 full-pipeline | TECH-ANCHOR.md(首版) |
| 第 2a 波 | PRD-Core 生成(9 章 66 项,子 Agent 分片完成) | PRD-Core.md |
| 第 2b 波 | 核心工程文档 11 份(ADD/MAP/DIS×2/EVENT/SECURITY/ERR/CFG/ADI/DEP) | 各文档 |
| 第 2.8 波 | 约束系列 8 份 + FLC + README 首版 | CONSTRAINTS-01~08 / FLC.md / README.md |
| 形态修订 | **外壳从网页版改为 Windows 桌面程序(pywebview)**——用户拍板;F065 重写、ADR-012 修订、MAP/DEP/README/CONSTRAINTS-01 同步 | PRD-Core F065 / ADD ADR-012 / MAP / DEP / README / CONSTRAINTS-01 / TECH-ANCHOR(变更记录) |
| 第 2.11 波 | specs/ 编码规格 32 模块(313 函数)+ 索引(4 批 8 子 Agent) | specs/*.md(32 份)/ specs/README.md |
| 第 3.8 波 | 文档站生成(56 页) | docs_html/ |
| 第 3 波 | OPS + SOP + KEY-FINDINGS + IMPACT-MATRIX | 4 份新文档 |
| 第 3.3 波 | 矛盾扫描修复:P0-1 EVENT-SCHEMA TLB-404→TLB-802;P1 ERR.md 补登 3 码;P0-3 errors.py 12 处码语义错位重写对齐 | EVENT-SCHEMA / ERR.md / pyharness/errors.py |
| 第 3.5 波 | README 全量刷新(补 4 新文档 + 校准体量);文档站重生成(60 页) | README.md / docs_html/ |
| 第 2a.5 波(补) | 参数锚定补建:SYSTEM-IDENTITY + PARAMETER-ANCHOR(此前遗漏,审计发现) | SYSTEM-IDENTITY.md / PARAMETER-ANCHOR.md |
| 收尾 | 交付审计:60 份 md / 1.24MB;代码阶段启动(errors.py 实现) | — |

## 版本归档说明
- v1.0 首个完整交付(全流程含质量闭环)
- 后续:代码阶段每完成一阶段模块,对应 specs 状态列更新;MAJOR 文档重写时归档旧版至 archive/
