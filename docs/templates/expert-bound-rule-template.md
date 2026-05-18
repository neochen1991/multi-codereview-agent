# 专家绑定检视规范模板

> 用途：上传并绑定到某个专家后，系统会把每个 `## RULE:` 规则块编译为结构化 RuleCard，再用于 minimax-2.5 等模型的短 prompt 检视。
> 重点：不要写成长篇原则文档；每条规则都要可执行、可定位证据、可判断误报。

## RULE: ARCH-JDDD-002

### Title
应用服务不得绕过聚合工厂直接构造聚合根

### Scope
- language: java
- expert: ddd_architecture
- layer: application-service

### Trigger Signals
- `new Course(`
- changed file path contains `/application/`
- aggregate root creation logic changed

### Must Check
- 检查应用服务是否直接 `new` 聚合根。
- 检查聚合根或领域服务中是否存在工厂方法。
- 检查直接构造是否绕过不变量校验、领域事件或默认状态初始化。

### Required Context
- changed_file_full_content
- aggregate_root_definition
- aggregate_factory_method
- domain_event_publication

### Evidence Required
- 指出直接构造聚合根的具体代码行。
- 指出应该被调用的工厂方法或创建入口。
- 说明被绕过的不变量、领域事件或初始化逻辑。

### False Positive Guards
- 如果该构造函数本身就是唯一公开且被规范认可的创建入口，不要报告。
- 如果被构造的类不是聚合根，不要报告。
- 如果本次 diff 只是测试代码构造 fixture，不要按生产代码问题报告。

### Severity
major

### Normalized Issue Type
aggregate_factory_bypassed

### Good Example
```java
Course course = Course.create(id, name);
```

### Bad Example
```java
Course course = new Course(id, name);
```

---

## 字段要求

1. `Scope` 至少包含 `language`，专家绑定规则建议包含 `expert`。
2. `Must Check` 写模型必须逐条核对的动作，不写抽象口号。
3. `Required Context` 写审查成立前必须读取的上下文；缺失时系统应输出 `insufficient_context`。
4. `Evidence Required` 写成立 finding 必须具备的证据。
5. `False Positive Guards` 写不能报问题的条件；缺失时规则会进入 `needs_review`。
6. `Normalized Issue Type` 使用稳定英文短语，便于去重、评测和统计。
