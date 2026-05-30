import { humanizeReviewText, stripReviewSupplementSections } from "@/utils/displayText";

const INTERNAL_TEXT_PATTERNS: RegExp[] = [
  /baseline/i,
  /related_findings/i,
  /validator_failed/i,
  /consistency[_\s-]*validation/i,
  /Static diff signals/i,
  /issue\.issue\./i,
  /当前未记录/,
  /当前未返回/,
  /当前未识别/,
  /当前未生成/,
  /当前问题已定位到代码改动/,
  /后端未返回/,
  /系统没有生成/,
  /不应直接提交/,
  /请先补齐证据/,
  /请结合审核结论补充修复方案/,
  /replace with actual patched code/i,
  /placeholder/i,
  /risk_hypothesis/i,
  /^分类[：:].*置信度理由/,
  /原始置信度/,
  /仍需复核/,
  /^定位\s+.*主责/,
  /伪代码/,
  /占位/,
  /根据实际/,
  /请结合实际/,
  /当前变更在/,
  /代码锚点单独修复/,
  /按命中的规则修正当前代码/,
  /定位候选代码行/,
  /按命中规则修正实现/,
  /需要(进一步)?确认其他条件/,
  /需(要)?结合.*确认/,
  /^确认.*(依赖类型|目标行为|是否|条件)/,
  /^需(要)?.*确认/,
  /无法确认.*是否/,
  /不确定.*是否/,
];

const INTERNAL_LINE_PREFIXES = [
  "问题汇总",
  "修复建议汇总",
  "定向辩论预裁决",
  "Judge",
  "结果复核",
  "审核调度",
  "Static diff signals",
];

const normalizeContext = (...values: Array<string | null | undefined>): string =>
  values.map((item) => String(item || "")).join("\n").toLowerCase();

export const rewriteUserFacingIssueText = (value?: string | null, context?: string | null): string => {
  let text = humanizeReviewText(String(value || "")).trim();
  if (!text) return "";
  const ctx = normalizeContext(value, context);

  const paymentFailureAsSuccess =
    /payment|settlement|capture|gateway|payments|settlementresult\.success|支付|结算/.test(ctx) &&
    /catch|ignored|exception|runtimeexception|异常|success|成功/.test(ctx);
  if (/异常被(静默)?吞(掉|没)|异常被忽略|exception.*swallow/i.test(text)) {
    text = paymentFailureAsSuccess
      ? text.replace(/异常被(静默)?吞(掉|没)|异常被忽略|exception.*swallow/gi, "支付结算失败后仍返回成功")
      : text.replace(/异常被(静默)?吞(掉|没)|异常被忽略|exception.*swallow/gi, "失败被忽略，调用方会误以为处理成功");
  }
  text = text.replace(/异常被吞掉后仍返回成功/g, paymentFailureAsSuccess ? "支付结算失败后仍返回成功" : "失败被忽略后仍按成功处理");
  if (/异常返回语义被弱化|失败语义被弱化|异常.*返回.*成功/.test(text)) {
    text = paymentFailureAsSuccess ? "支付结算失败后仍返回成功" : "失败被忽略后仍按成功处理";
  }
  if (/应用服务泄漏支付网关协议/.test(text) && paymentFailureAsSuccess) {
    text = "支付结算失败后仍返回成功";
  }

  if (/承诺未落地/.test(text)) {
    text = /todo|扣减库存|预占|inventory|reserve/.test(ctx)
      ? text.replace(/承诺未落地/g, "TODO 里的库存扣减未实现")
      : text.replace(/承诺未落地/g, "注释写了要做，但代码未实现");
  }
  text = text.replace(/查询边界缺失/g, "查询没有分页限制");
  text = text.replace(/并发保护被移除/g, "批量报名的锁保护被移除");
  text = text.replace(/领域事件发布顺序早于聚合持久化/g, "课程保存前就发布了领域事件");
  text = text.replace(/批量写入从 saveAll 退化为循环逐条 repository\.save/g, () => {
    if (/paymentsettlementservice|支付|结算/.test(ctx)) return "支付批量结算从 saveAll 退化为循环逐条保存";
    if (/bulkenrollmentservice|报名/.test(ctx)) return "批量报名从 saveAll 退化为循环逐条保存";
    return "批量保存改成了循环逐条保存";
  });
  return text;
};

export const cleanUserFacingText = (value?: string | null): string => {
  const raw = String(value || "").trim();
  if (!raw) return "";
  let text = stripReviewSupplementSections(humanizeReviewText(raw))
    .replace(/^[-*]\s*/gm, "")
    .replace(/\r\n/g, "\n")
    .trim();
  const lines = text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .filter((line) => !INTERNAL_LINE_PREFIXES.some((prefix) => line.startsWith(prefix)))
    .filter((line) => !INTERNAL_TEXT_PATTERNS.some((pattern) => pattern.test(line)));
  text = lines.join("\n").trim();
  if (!text || text === "-") return "";
  if (INTERNAL_TEXT_PATTERNS.some((pattern) => pattern.test(text))) return "";
  if (/^[\[{]/.test(text)) return "";
  return rewriteUserFacingIssueText(text);
};

export const cleanUserFacingList = (values?: Array<string | null | undefined> | null): string[] =>
  Array.from(new Set((values || []).map((item) => cleanUserFacingText(item)).filter(Boolean)));

export const pickUserFacingText = (
  values: Array<string | null | undefined>,
  fallback = "",
): string => {
  for (const value of values) {
    const cleaned = cleanUserFacingText(value);
    if (cleaned) return cleaned;
  }
  return fallback;
};

type IssueDisplaySource = {
  title?: string | null;
  summary?: string | null;
  file_path?: string | null;
  line_start?: number | null;
  finding_type?: string | null;
  normalized_issue_type?: string | null;
  category_label?: string | null;
  remediation_strategy?: string | null;
  remediation_suggestion?: string | null;
  remediation_steps?: Array<string | null | undefined> | null;
  metaSummary?: string | null;
};

const ISSUE_TYPE_LABELS: Record<string, string> = {
  n_plus_one: "循环内逐条访问",
  loop_call_amplification: "循环内重复调用",
  bulk_processing_boundary_missing: "批量处理缺少边界",
  comment_contract_unimplemented: "注释承诺未实现",
  declared_intent_without_implementation: "声明的逻辑没有实现",
  comment_promise_unimplemented: "注释说明与代码不一致",
  lock_guard_removed: "并发保护被削弱",
  concurrency_guard_removed: "并发保护被移除",
  lock_scope_risk: "锁保护范围不完整",
  query_bound_removed: "查询没有分页限制",
  query_boundary_missing: "查询没有分页限制",
  unbounded_query: "查询没有分页限制",
  unbounded_query_risk: "查询结果可能过大",
  exception_swallowed: "失败被忽略后仍按成功处理",
  exception_semantics_weakened: "失败处理语义被弱化",
  course_creation_semantics: "领域创建流程有风险",
  aggregate_factory_bypass: "聚合工厂被绕过",
  aggregate_factory_bypassed: "聚合工厂被绕过",
  direct_defect: "直接缺陷",
  code_quality: "代码质量问题",
  risk_hypothesis: "待核对风险",
  test_gap: "测试覆盖缺口",
  design_concern: "设计一致性风险",
  security_risk: "安全风险",
  performance_risk: "性能风险",
};

const GENERIC_TITLE_PATTERNS: RegExp[] = [
  /^[-_a-z0-9]+$/i,
  /^问题\s*\d*$/i,
  /^待验证风险$/,
  /^直接缺陷$/,
  /^代码质量问题$/,
  /^待核对风险$/,
  /^risk_hypothesis$/i,
  /^code_quality$/i,
  /^代码问题$/,
  /^检视发现$/,
  /^需要人工确认$/,
  /^风险提示$/,
];

const normalizeIssueTypeKey = (value?: string | null): string =>
  String(value || "")
    .trim()
    .toLowerCase()
    .replace(/\s+/g, "_")
    .replace(/-/g, "_");

const shortFileName = (filePath?: string | null): string => {
  const value = String(filePath || "").replace(/\\/g, "/").trim();
  return value.split("/").filter(Boolean).pop() || "";
};

export const hasCrossContextPollution = (value?: string | null, filePath?: string | null): boolean => {
  const text = String(value || "").toLowerCase();
  const path = String(filePath || "").toLowerCase();
  if (!text || !path) return false;
  const contextMarkers: Record<string, string[]> = {
    paymentsettlementservice: ["coursecreator", "bulkenrollmentservice", "报名", "库存", "聚合构造", "course.create"],
    bulkenrollmentservice: ["paymentsettlementservice", "coursecreator", "支付网关", "支付→", "支付-", "支付到", "结算", "course.create"],
    coursecreator: ["paymentsettlementservice", "bulkenrollmentservice", "支付网关", "报名", "库存"],
  };
  const current = Object.keys(contextMarkers).find((marker) => path.includes(marker));
  return Boolean(current && contextMarkers[current].some((marker) => text.includes(marker)));
};

const firstReadableSentence = (value?: string | null, maxLength = 180): string => {
  const text = cleanUserFacingText(value);
  if (!text) return "";
  const flattened = text.replace(/\s*\n+\s*/g, " ").trim();
  const sentence = flattened.split(/(?<=[。！？；;.!?])\s+/)[0] || flattened;
  return sentence.length > maxLength ? `${sentence.slice(0, maxLength - 1)}…` : sentence;
};

const isGenericTitle = (value?: string | null): boolean => {
  const text = cleanUserFacingText(value);
  if (!text) return true;
  if (text.length > 96) return true;
  if (!/[\u4e00-\u9fa5]/.test(text)) return true;
  return GENERIC_TITLE_PATTERNS.some((pattern) => pattern.test(text));
};

export const issueTypeDisplayLabel = (...values: Array<string | null | undefined>): string => {
  let genericFallback = "";
  const context = values.map((item) => String(item || "")).join("\n");
  if (
    /payment|settlement|支付|结算|支付网关/i.test(context) &&
    /catch|ignored|exception|runtimeexception|异常|失败语义|返回语义|success|成功/i.test(context)
  ) {
    return "支付失败被当成成功";
  }
  if (/Course\.create|new Course|构造函数|聚合工厂|工厂方法|aggregate\s*factory/i.test(context)) {
    return "聚合工厂被绕过";
  }
  for (const value of values) {
    const raw = cleanUserFacingText(rewriteUserFacingIssueText(value, context));
    if (!raw) continue;
    const key = normalizeIssueTypeKey(raw);
    if (ISSUE_TYPE_LABELS[key]) {
      const mapped = ISSUE_TYPE_LABELS[key];
      if (["直接缺陷", "代码质量问题", "待核对风险"].includes(mapped)) {
        genericFallback ||= mapped;
        continue;
      }
      return mapped;
    }
    if (/n\+1/i.test(raw)) return "N+1 查询风险";
    if (/saveall|repository\.save|per[-\s]?row|循环.*保存|逐条保存|循环内.*写入/i.test(raw)) return "循环内逐条写入";
    if (/limit|分页|大结果集|unbounded/i.test(raw)) return "查询缺少边界控制";
    if (/like|模糊匹配|contains|searchPendingByCourseLike/i.test(raw)) return "查询条件被放宽";
    if (/todo|扣减库存|预占|inventory|reserve/i.test(raw)) return "注释承诺未实现";
    if (/注释|comment/.test(raw) && /实现|落地|promise|contract/.test(raw)) return "注释承诺未实现";
    if (/锁|并发|synchronized|lock/i.test(raw)) return "并发保护风险";
    if (/循环|批量/.test(raw) && /查询|调用|性能|保存|写入/.test(raw)) return "循环路径性能风险";
    if (/catch|ignored|success|异常|exception|失败被忽略|支付结算失败/i.test(raw)) {
      return /payment|settlement|支付|结算/i.test(context) ? "支付失败被当成成功" : "失败被忽略后仍按成功处理";
    }
    if (/event published before persistence|transactional outbox|持久化前.*事件|事件.*持久化/i.test(raw)) return "领域事件发布顺序错误";
    if (/Course\.create|new Course|构造函数|聚合.*工厂|工厂.*聚合|工厂方法|创建路径变更|aggregate|factory/i.test(raw)) return "聚合工厂被绕过";
    if (/domain event|CourseCreatedDomainEvent|领域事件/i.test(raw)) return "领域事件风险";
    if (/测试|test/i.test(raw)) return "测试覆盖缺口";
    if (/安全|权限|鉴权|sql注入|xss/i.test(raw)) return "安全风险";
    if (/[\u4e00-\u9fa5]/.test(raw) && !isGenericTitle(raw)) return raw;
  }
  if (genericFallback && genericFallback !== "直接缺陷") {
    return genericFallback;
  }
  if (genericFallback === "直接缺陷") {
    return "代码风险";
  }
  for (const value of values) {
    const raw = cleanUserFacingText(value);
    if (raw && /[\u4e00-\u9fa5]/.test(raw) && !isGenericTitle(raw)) {
      return raw;
    }
  }
  return "代码风险";
};

export const issueTextMatchesIssueType = (issueType?: string | null, value?: string | null): boolean => {
  const type = normalizeIssueTypeKey(issueType);
  const text = String(value || "").toLowerCase().replace(/\s+/g, "");
  if (!type || !text) return false;
  const tokenMap: Array<[RegExp, string[]]> = [
    [/exception|swallowed|异常|失败/, ["catch", "exception", "runtimeexception", "ignored", "静默吞", "吞掉", "返回成功", "settlementresult.success"]],
    [/comment|declared_intent|promise|承诺/, ["todo", "fixme", "注释", "承诺", "未实现", "没有实现", "扣减库存"]],
    [/lock|concurr|并发/, ["synchronized", "lockregistry", "lockfor", "锁", "并发保护", "加锁"]],
    [/query_bound|unbounded|boundary|分页|边界/, ["limit", "pageable", "pagerequest", "分页", "全量", "全表", "边界"]],
    [/n_plus_one|loop|bulk|performance|循环/, ["n+1", "循环", "逐条", "repository.save", "saveall", "批量", "loop_call"]],
    [/course_creation|aggregate|factory|domain_event|ddd|聚合/, ["course.create", "newcourse", "聚合工厂", "聚合根", "领域事件", "domainevent", "coursecreateddomainevent"]],
    [/query_semantics|like|equal|语义/, ["builder.like", "builder.equal", "精确匹配", "模糊匹配", "查询语义"]],
  ];
  const matched = tokenMap.find(([pattern]) => pattern.test(type));
  if (!matched) return false;
  return matched[1].some((token) => text.includes(token));
};

export const buildReadableIssueTitle = (source: IssueDisplaySource): string => {
  const context = normalizeContext(source.title, source.summary, source.category_label, source.normalized_issue_type, source.finding_type, source.file_path);
  const evidenceContext = normalizeContext(source.summary, source.category_label, source.normalized_issue_type, source.finding_type, source.file_path);
  const title = cleanUserFacingText(rewriteUserFacingIssueText(source.title, context));
  const titleLooksEventOrder = /发布.*(持久化|保存)|保存前.*发布|持久化前.*发布/.test(title);
  const contextLooksFactoryBypass = /Course\.create|new Course|构造函数|聚合工厂|工厂方法|aggregate|factory/i.test(evidenceContext);
  const contextLooksEventOrder = /(publish|发布).{0,24}(save|持久化|保存)|(save|持久化|保存).{0,24}(publish|发布)/i.test(evidenceContext);
  if (titleLooksEventOrder && contextLooksFactoryBypass && !contextLooksEventOrder) {
    return "绕过聚合工厂创建聚合根";
  }
  if (title && !isGenericTitle(title)) return firstReadableSentence(title, 80);
  const label = issueTypeDisplayLabel(source.title, source.summary, source.category_label, source.normalized_issue_type, source.finding_type);
  const fileName = shortFileName(source.file_path);
  const location = fileName ? `${fileName}${source.line_start ? `:${source.line_start}` : ""}` : "";
  return location ? `${label}（${location}）` : label;
};

export const buildReadableIssueSummary = (source: IssueDisplaySource): string => {
  const context = normalizeContext(source.title, source.summary, source.category_label, source.normalized_issue_type, source.finding_type, source.file_path);
  const issueSignal = normalizeContext(source.title, source.category_label, source.normalized_issue_type, source.finding_type);
  const summarySource =
    hasCrossContextPollution(source.summary, source.file_path) || !issueTextMatchesIssueType(issueSignal, source.summary)
      ? ""
      : source.summary;
  const summary = firstReadableSentence(rewriteUserFacingIssueText(summarySource, context), 220);
  const fileName = shortFileName(source.file_path);
  const location = fileName ? `${fileName}${source.line_start ? ` 第 ${source.line_start} 行` : ""}` : "对应代码";
  if (summary && !isGenericTitle(summary)) {
    if (/^支付结算失败后仍返回成功$/.test(summary)) {
      return `${location} 的异常处理会把失败路径当成成功返回，调用方无法感知真实失败。`;
    }
    return summary;
  }
  const meta = firstReadableSentence(rewriteUserFacingIssueText(source.metaSummary, context), 220);
  if (meta && !isGenericTitle(meta)) return meta;
  const label = issueTypeDisplayLabel(source.title, source.summary, source.category_label, source.normalized_issue_type, source.finding_type);
  if (/支付失败|失败被忽略|异常/.test(label)) {
    return `${location} 的异常处理会把失败路径当成成功返回，调用方无法感知真实失败。`;
  }
  if (/查询缺少边界|分页|LIMIT/i.test(label)) {
    return `${location} 的查询缺少分页、LIMIT 或固定窗口边界，数据量放大后可能返回大结果集。`;
  }
  if (/注释承诺/.test(label)) {
    return `${location} 的注释或 TODO 已经承诺业务动作，但当前实现没有对应代码。`;
  }
  if (/并发保护|锁/.test(label)) {
    return `${location} 移除了原有并发保护，批量或并发调用时可能出现重复处理或状态竞争。`;
  }
  if (/循环|逐条|N\+1/i.test(label)) {
    return `${location} 在循环内逐条访问仓储、网关或保存接口，批量输入会被放大为 N 次外部访问。`;
  }
  if (/聚合.*工厂|工厂.*聚合|创建路径变更|DDD/.test(label)) {
    return `${location} 绕过聚合工厂创建聚合根，原先由工厂封装的不变量校验或领域事件记录可能丢失。`;
  }
  if (/领域事件/.test(label)) {
    return `${location} 的领域事件记录或发布路径发生变化，可能影响下游订阅方感知课程创建结果。`;
  }
  return `${location} 命中「${label}」风险，请按当前代码片段定位具体分支并修复。`;
};

export const buildReadableFixSummary = (source: IssueDisplaySource): string => {
  const context = normalizeContext(source.title, source.summary, source.category_label, source.normalized_issue_type, source.finding_type, source.file_path);
  const direct = rewriteUserFacingIssueText(pickUserFacingText([source.remediation_strategy, source.remediation_suggestion]), context);
  if (direct) return firstReadableSentence(direct, 220);
  const steps = cleanUserFacingList(source.remediation_steps).join("；");
  if (steps) return firstReadableSentence(steps, 220);
  const label = issueTypeDisplayLabel(source.title, source.summary, source.category_label, source.normalized_issue_type, source.finding_type);
  if (/支付失败|失败被忽略|异常/.test(label)) {
    return "不要在 catch 分支返回成功；保留异常上下文，改为抛出异常、返回明确失败结果或进入补偿流程。";
  }
  if (/查询缺少边界|分页|LIMIT/i.test(label)) {
    return "为查询补回分页、LIMIT 或固定批次窗口，并增加大数据量场景的回归用例。";
  }
  if (/注释承诺/.test(label)) {
    return "补齐注释或 TODO 承诺的业务动作；如果本次不交付，应删除误导性注释并拆出明确任务。";
  }
  if (/并发保护|锁/.test(label)) {
    return "恢复原有锁保护，或补上等价的幂等、唯一约束、分布式锁等并发控制，并增加并发提交用例。";
  }
  if (/循环|逐条|N\+1/i.test(label)) {
    return "把循环内逐条访问改回批量查询/批量保存，必要时按固定窗口分批处理。";
  }
  if (/聚合.*工厂|工厂.*聚合|创建路径变更|DDD/.test(label)) {
    return "改用聚合工厂方法创建聚合根，保证不变量校验和领域事件记录仍由聚合内部统一完成。";
  }
  if (/领域事件/.test(label)) {
    return "恢复由聚合内部记录领域事件的路径，或补齐等价事件发布逻辑，并增加订阅方可收到事件的回归用例。";
  }
  return `按「${label}」对应的代码位置补齐修复，并增加能复现该风险的回归测试。`;
};

export const isConcreteDisplayCode = (value?: string | null): boolean => {
  const code = String(value || "").trim();
  if (!code) return false;
  const lower = code.toLowerCase();
  const invalidMarkers = [
    "当前还没有生成建议修改代码",
    "当前未生成",
    "please verify against real source",
    "replace with actual patched code",
    "# suggested rewrite for",
    "todo:",
    "placeholder",
    "伪代码",
    "占位",
    "根据实际",
    "请结合实际",
    "separate validation from execution",
    "return early on invalid input",
    "keep the happy path flat and testable",
  ];
  if (invalidMarkers.some((marker) => lower.includes(marker))) return false;
  const lines = code.split("\n").map((line) => line.trim()).filter(Boolean);
  if (lines.length && lines.every((line) => line.startsWith("//") || line.startsWith("#") || line.startsWith("*"))) {
    return false;
  }
  return true;
};
