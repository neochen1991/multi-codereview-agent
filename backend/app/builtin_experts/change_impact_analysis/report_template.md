# 关联影响分析报告

## 1. 报告结论
{{summary}}

## 2. 本次变更概览
- 代码仓：{{repo_name}}
- 变更文件数：{{changed_file_count}}
- 变更文件：{{changed_files}}
- 关键变更符号：{{changed_symbols}}
- 图谱事实来源：{{fact_source}}

## 3. 关键关联影响
{{key_impact_points}}

## 4. 关键调用链路
{{impact_paths}}

## 5. 数据与事务影响
### 5.1 数据访问层影响
{{data_access_impact}}

### 5.2 事务边界影响
{{transaction_impact}}

### 5.3 缓存 / 消息 / 异步任务影响
{{integration_impact}}

## 6. 受影响文件与模块
### 6.1 受影响文件
{{impacted_files}}

### 6.2 受影响模块
{{impacted_modules}}

### 6.3 入口与外部触点
{{external_entrypoints}}

## 7. 建议测试范围
### 7.1 必须优先测试
{{must_test}}

### 7.2 建议补充测试
{{should_test}}

### 7.3 建议人工确认
{{manual_checks}}

## 8. 分析依据
### 8.1 GitNexus 工作流
{{analysis_workflow}}

### 8.2 本次实际查询对象
{{queried_targets}}

### 8.3 图谱确认到的风险级别
{{risk_level}}

## 9. 使用边界
{{limitations}}
