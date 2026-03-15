# Design Consistency Check

## Purpose
检查本次审核绑定的 IT 详细设计文档与当前代码实现是否一致。

## When To Use
- 当前专家为 `correctness_business`
- 本次 review 绑定了 `design_spec` 类型文档
- 改动命中了 service / usecase / handler / workflow / transformer / output 等业务实现文件

## Required Tools
- `diff_inspector`
- `repo_context_search`
- `design_spec_alignment`

## Rules
- 不允许脱离设计文档做需求猜测
- 不允许只根据命名推断实现完成度
- 证据不足时只能输出“待验证风险”
- 必须同时对照 diff、源码仓上下文和设计文档结构化结果

## Output Contract
- `design_alignment_status`
- `matched_design_points`
- `missing_design_points`
- `extra_implementation_points`
- `design_conflicts`
