# 系统能力说明

## 核心能力

- 创建 `MR / Branch` 审核任务，并统一归一化为 `ReviewSubject`
- 多专家协同审核：正确性、DDD、数据库、缓存、消息、安全、性能、测试、前端可访问性等
- LangGraph-style 编排：变更切片、上下文扩展、专家路由、冲突检测、证据核验、裁决合并
- 人工裁决 gate：高风险问题可进入人工确认流程
- GitNexus 关联影响分析：为 MR 输出独立影响报告和建议回归范围
- Repo 级审核策略：支持 `.ai-review.yml` 控制阈值、预算、专家启用和质量门禁
- 离线质量评估：用固定 fixtures 衡量 recall、precision、误报率、证据链覆盖率和成本
- 插件扩展：`extensions/skills` + `extensions/tools` 支持新增专家能力和本地工具

## 结果页语义

结果页按三层展示：

- `审核发现`：专家原始 finding，允许保留待验证风险
- `有效问题`：通过过滤和裁决后可进入整改的问题
- `被过滤的问题`：保留证据，但未升级为有效问题

进入有效问题清单的 issue 会带：

- `primary_expert_id`
- 严重级别
- 置信度
- 证据链
- 修复建议
- 过滤或裁决信息

## 质量治理入口

当前质量治理链路包含：

- 固定评测集：`backend/tests/fixtures/review_eval_cases/`
- 固定评测结果：`backend/tests/fixtures/review_eval_results/`
- 评估脚本：`scripts/eval_review_quality.py`
- 门禁脚本：`scripts/check_review_quality.sh`
- 结果导出：`scripts/export_review_eval_result.py`
- Repo 策略模板：`docs/templates/ai-review-policy-template.yml`

运行质量门禁：

```bash
bash scripts/check_review_quality.sh
```

## 插件扩展

扩展目录约定：

```text
extensions/
  skills/
    <skill>/
      SKILL.md
      metadata.json
  tools/
    <tool>/
      tool.json
      run.py
```

新增专家能力通常只需要：

1. 新增 `extensions/skills/<skill>/SKILL.md`
2. 新增 `extensions/skills/<skill>/metadata.json`
3. 在 `metadata.json` 声明 `bound_experts`
4. 如需本地执行能力，再新增 `extensions/tools/<tool>/tool.json` 和 `run.py`

## 代码目录

```text
backend/
  app/
    api/routes/
    domain/models/
    repositories/
    services/
frontend/
  src/
    components/
    pages/
    services/
docs/
  architecture/
  plans/
  templates/
scripts/
```

## 相关文档

- [GitNexus 关联影响分析说明](2026-05-01-gitnexus-impact-analysis.md)
- [专家 Agent 职责边界手册](2026-04-19-expert-agent-boundary-handbook.md)
- [Review Quality Eval Baseline](2026-05-01-review-quality-eval-baseline.md)
- [Repo Review Policy](2026-05-01-repo-review-policy.md)
- [系统运行与代码地图](code-wiki.md)
