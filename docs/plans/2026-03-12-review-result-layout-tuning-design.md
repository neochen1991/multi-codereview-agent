# 结论与行动页布局调整设计

## 目标

- 报告摘要与人工裁决并排展示，视觉上属于同一层级。
- 问题清单扩展为主内容区，避免右侧常驻问题详情占用宽度。
- 问题详情改成按需弹窗查看，聚焦单条问题。
- 审核对象下沉到问题清单下方，形成更自然的阅读顺序。
- 报告摘要中的关键数字改成用户更容易理解的统计口径。

## 布局方案

### 顶部

- 左侧：`Code Review 报告摘要`
- 右侧：`人工裁决`
- 两张卡同一行、同高度

### 中部

- `Code Review 问题清单` 占整行宽度
- 点击问题后弹出详情弹窗

### 底部

- `审核对象`
- `产物摘要`

## 统计口径

- 总问题数
- 高风险问题
- 待人工裁决
- 已核验问题

## 影响范围

- `frontend/src/pages/ReviewWorkbench/index.tsx`
- `frontend/src/components/review/ReportSummaryPanel.tsx`
- `frontend/src/components/review/HumanGatePanel.tsx`
- `frontend/src/components/review/FindingsPanel.tsx`
- `frontend/src/components/review/CodeReviewConclusionPanel.tsx`
