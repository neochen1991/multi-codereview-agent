const path = require("path");
const PptxGenJS = require("pptxgenjs");
const {
  warnIfSlideHasOverlaps,
  warnIfSlideElementsOutOfBounds,
} = require("./pptxgenjs_helpers/layout");

const pptx = new PptxGenJS();
pptx.layout = "LAYOUT_WIDE";
pptx.author = "Codex";
pptx.company = "OpenAI";
pptx.subject = "Multi Code Review Agent v3";
pptx.title = "多专家代码审查系统简介 v3";
pptx.lang = "zh-CN";
pptx.theme = {
  headFontFace: "Aptos",
  bodyFontFace: "Aptos",
  lang: "zh-CN",
};

const OUT_DIR = __dirname;
const OUT_FILE = path.join(OUT_DIR, "multi-code-review-agent-intro-v3.pptx");

const C = {
  bg: "F7F5F1",
  bgAlt: "FCFBF8",
  ink: "17324D",
  soft: "5B7186",
  line: "D9E2EA",
  white: "FFFFFF",
  blue: "4F7BFF",
  blueSoft: "EEF4FF",
  teal: "0F766E",
  tealSoft: "E8F8F5",
  orange: "F97316",
  orangeSoft: "FFF1E8",
  gold: "D97706",
  goldSoft: "FFF6E6",
  rose: "E11D48",
  roseSoft: "FFF0F4",
  navy: "10253A",
};

function addCanvas(slide, bg = C.bgAlt) {
  slide.background = { color: bg };
  slide.addShape(pptx.ShapeType.rect, {
    x: 0,
    y: 0,
    w: 13.333,
    h: 7.5,
    line: { color: bg, transparency: 100 },
    fill: { color: bg },
  });
}

function addHeader(slide, label, title, subtitle, page, accent = C.blue) {
  slide.addShape(pptx.ShapeType.rect, {
    x: 0,
    y: 0,
    w: 13.333,
    h: 0.14,
    line: { color: C.navy, transparency: 100 },
    fill: { color: C.navy },
  });
  slide.addShape(pptx.ShapeType.roundRect, {
    x: 0.58,
    y: 0.44,
    w: 1.18,
    h: 0.26,
    rectRadius: 0.06,
    line: { color: accent, transparency: 100 },
    fill: { color: accent },
  });
  slide.addText(label, {
    x: 0.64,
    y: 0.495,
    w: 1.06,
    h: 0.12,
    fontSize: 8,
    bold: true,
    color: C.white,
    align: "center",
    margin: 0,
  });
  slide.addText(page, {
    x: 11.9,
    y: 0.47,
    w: 0.8,
    h: 0.14,
    fontSize: 9,
    color: C.soft,
    bold: true,
    align: "right",
    margin: 0,
  });
  slide.addText(title, {
    x: 0.58,
    y: 0.88,
    w: 9.8,
    h: 0.42,
    fontSize: 24,
    bold: true,
    color: C.ink,
    margin: 0,
  });
  if (subtitle) {
    slide.addText(subtitle, {
      x: 0.58,
      y: 1.36,
      w: 10.5,
      h: 0.3,
      fontSize: 10,
      color: C.soft,
      margin: 0,
    });
  }
}

function addCard(slide, x, y, w, h, fill = C.white, line = C.line) {
  slide.addShape(pptx.ShapeType.roundRect, {
    x,
    y,
    w,
    h,
    rectRadius: 0.08,
    line: { color: line, pt: 1 },
    fill: { color: fill },
    shadow: { type: "outer", color: "DDE5EC", blur: 1, angle: 45, distance: 0.5, opacity: 0.1 },
  });
}

function addTag(slide, x, y, w, text, fill, color = C.ink) {
  slide.addShape(pptx.ShapeType.roundRect, {
    x,
    y,
    w,
    h: 0.24,
    rectRadius: 0.06,
    line: { color: fill, transparency: 100 },
    fill: { color: fill },
  });
  slide.addText(text, {
    x: x + 0.08,
    y: y + 0.04,
    w: w - 0.16,
    h: 0.12,
    fontSize: 8,
    bold: true,
    color,
    align: "center",
    margin: 0,
  });
}

function addBulletList(slide, x, y, w, items, color = C.ink, size = 10.5, bulletColor = C.blue) {
  let top = y;
  items.forEach((item) => {
    slide.addShape(pptx.ShapeType.ellipse, {
      x,
      y: top + 0.06,
      w: 0.08,
      h: 0.08,
      line: { color: bulletColor, transparency: 100 },
      fill: { color: bulletColor },
    });
    slide.addText(item, {
      x: x + 0.14,
      y: top,
      w: w - 0.14,
      h: 0.28,
      fontSize: size,
      color,
      margin: 0,
      breakLine: false,
    });
    top += 0.34;
  });
}

function addArrow(slide, x1, y1, x2, y2, color = C.blue) {
  slide.addShape(pptx.ShapeType.line, {
    x: x1,
    y: y1,
    w: x2 - x1,
    h: y2 - y1,
    line: { color, pt: 2, beginArrowType: "none", endArrowType: "triangle" },
  });
}

function finalize(slide) {
  warnIfSlideHasOverlaps(slide, pptx);
  warnIfSlideElementsOutOfBounds(slide, pptx);
}

function coverSlide() {
  const slide = pptx.addSlide();
  addCanvas(slide, C.bg);
  slide.addShape(pptx.ShapeType.rect, {
    x: 0,
    y: 0,
    w: 13.333,
    h: 0.16,
    line: { color: C.navy, transparency: 100 },
    fill: { color: C.navy },
  });
  slide.addText("多专家代码审查系统", {
    x: 0.7,
    y: 0.72,
    w: 5.7,
    h: 0.38,
    fontSize: 14,
    bold: true,
    color: C.blue,
    margin: 0,
  });
  slide.addText("让复杂 MR 审查从“凭经验”走向“有章法”", {
    x: 0.7,
    y: 1.18,
    w: 6.2,
    h: 0.92,
    fontSize: 27,
    bold: true,
    color: C.ink,
    margin: 0,
    breakLine: true,
  });
  slide.addText("它不是替代人工 Review，而是把资深同学在复杂 MR 审查里的判断方法，逐步沉淀成团队可复用的能力。", {
    x: 0.7,
    y: 2.2,
    w: 5.7,
    h: 0.48,
    fontSize: 11,
    color: C.soft,
    margin: 0,
  });
  addTag(slide, 0.72, 2.92, 2.2, "当前定位：内部辅助系统", C.blueSoft, C.blue);

  addCard(slide, 0.68, 3.4, 5.92, 2.72, C.white);
  slide.addText("为什么这件事值得做", {
    x: 0.96,
    y: 3.68,
    w: 2.6,
    h: 0.2,
    fontSize: 13,
    bold: true,
    color: C.ink,
    margin: 0,
  });
  addCard(slide, 1.0, 4.14, 2.0, 1.3, C.orangeSoft, C.orange);
  addCard(slide, 4.18, 4.14, 2.0, 1.3, C.tealSoft, C.teal);
  slide.addText("复杂 MR 容易漏看重点", {
    x: 1.18,
    y: 4.36,
    w: 1.62,
    h: 0.18,
    fontSize: 11.5,
    bold: true,
    color: C.ink,
    align: "center",
    margin: 0,
  });
  slide.addText("业务、架构、数据库问题常常叠在一起", {
    x: 1.18,
    y: 4.64,
    w: 1.62,
    h: 0.34,
    fontSize: 9.2,
    color: C.soft,
    align: "center",
    margin: 0,
  });
  slide.addText("先拆成多个专业视角", {
    x: 4.36,
    y: 4.36,
    w: 1.62,
    h: 0.18,
    fontSize: 11.5,
    bold: true,
    color: C.ink,
    align: "center",
    margin: 0,
  });
  slide.addText("让不同专家分别看业务、设计、数据库和性能", {
    x: 4.36,
    y: 4.64,
    w: 1.62,
    h: 0.36,
    fontSize: 9.2,
    color: C.soft,
    align: "center",
    margin: 0,
  });
  addArrow(slide, 3.1, 4.78, 4.0, 4.78, C.blue);

  addCard(slide, 6.96, 0.76, 5.68, 5.46, C.white);
  slide.addText("这套系统想解决什么", {
    x: 7.28,
    y: 1.0,
    w: 2.8,
    h: 0.2,
    fontSize: 13,
    bold: true,
    color: C.ink,
    margin: 0,
  });
  const metrics = [
    ["复杂问题不再只靠个人经验兜底", "先拆出这次 MR 涉及的领域，再分别交给对应专家去看", C.blueSoft],
    ["资深同学的审查方法可以沉淀下来", "规则、规范、经验和取证能力，会逐步沉淀在专家侧", C.tealSoft],
    ["审查不是泛泛提醒，而是补证据后再判断", "每个专家会按职责拿到自己的代码上下文、规则和证据", C.goldSoft],
    ["最后留下的是研发可以处理的问题清单", "系统会做去重、收敛和阈值过滤，把真正值得优先处理的问题整理出来", C.roseSoft],
  ];
  let y = 1.42;
  metrics.forEach(([title, body, fill]) => {
    addCard(slide, 7.26, y, 5.0, 0.95, fill, fill);
    slide.addText(title, {
      x: 7.46,
      y: y + 0.14,
      w: 4.56,
      h: 0.18,
      fontSize: 10.5,
      bold: true,
      color: C.ink,
      margin: 0,
    });
    slide.addText(body, {
      x: 7.46,
      y: y + 0.4,
      w: 4.56,
      h: 0.3,
      fontSize: 9.2,
      color: C.soft,
      margin: 0,
    });
    y += 1.06;
  });
  slide.addText("01 / 06", {
    x: 11.86,
    y: 6.76,
    w: 0.7,
    h: 0.16,
    fontSize: 9,
    bold: true,
    color: C.soft,
    align: "right",
    margin: 0,
  });
  finalize(slide);
}

function extensibilitySlide() {
  const slide = pptx.addSlide();
  addCanvas(slide);
  addHeader(slide, "Extensibility", "三种扩展方式，让新能力接入不用反复改主流程", "这一页只讲两件事：平时怎么扩，运行时怎么用。", "02 / 06");

  addCard(slide, 0.64, 1.85, 12.06, 1.6, C.white);
  slide.addText("平时怎么扩", {
    x: 0.92,
    y: 2.08,
    w: 1.4,
    h: 0.18,
    fontSize: 13,
    bold: true,
    color: C.ink,
    margin: 0,
  });
  const cols = [
    ["规范文档", "告诉专家该按什么规则看", "上传规范后，系统会拆章节、拆规则卡、建立索引", C.blueSoft, C.blue],
    ["Skill", "告诉系统什么场景启额外能力", "更像专家插件，用来补一类专项分析能力", C.tealSoft, C.teal],
    ["Tool", "告诉专家去哪里拿证据", "比如代码仓、设计文档、数据库表结构、外部平台", C.goldSoft, C.gold],
  ];
  let x = 0.92;
  cols.forEach(([title, line1, line2, fill, accent], idx) => {
    addCard(slide, x, 2.42, 3.55, 0.78, fill, fill);
    addTag(slide, x + 0.14, 2.55, 0.42, String(idx + 1), accent, C.white);
    slide.addText(title, {
      x: x + 0.66,
      y: 2.53,
      w: 1.0,
      h: 0.16,
      fontSize: 11.5,
      bold: true,
      color: C.ink,
      margin: 0,
    });
    slide.addText(line1, {
      x: x + 1.72,
      y: 2.53,
      w: 1.58,
      h: 0.16,
      fontSize: 9,
      color: C.soft,
      margin: 0,
    });
    slide.addText(line2, {
      x: x + 0.14,
      y: 2.84,
      w: 3.18,
      h: 0.16,
      fontSize: 8.8,
      color: C.soft,
      margin: 0,
    });
    x += 3.92;
  });

  addCard(slide, 0.64, 3.74, 12.06, 2.72, C.white);
  slide.addText("运行时怎么组装", {
    x: 0.92,
    y: 4.0,
    w: 1.8,
    h: 0.18,
    fontSize: 13,
    bold: true,
    color: C.ink,
    margin: 0,
  });
  const leftNodes = [
    ["MR 输入", 1.0, 4.52, C.blueSoft],
    ["命中规则", 1.0, 5.16, C.tealSoft],
    ["代码证据", 1.0, 5.8, C.goldSoft],
  ];
  leftNodes.forEach(([label, lx, ly, fill]) => {
    addCard(slide, lx, ly, 1.62, 0.44, fill, fill);
    slide.addText(label, {
      x: lx,
      y: ly + 0.14,
      w: 1.62,
      h: 0.12,
      fontSize: 9.4,
      bold: true,
      color: C.ink,
      align: "center",
      margin: 0,
    });
  });
  addCard(slide, 4.0, 4.72, 4.04, 1.2, C.blueSoft, C.blue);
  slide.addText("专家专属上下文", {
    x: 4.3,
    y: 5.0,
    w: 3.44,
    h: 0.18,
    fontSize: 14,
    bold: true,
    color: C.ink,
    align: "center",
    margin: 0,
  });
  slide.addText("不是把所有资料一股脑塞进去，而是只把这次 MR 真相关的内容拼起来。", {
    x: 4.3,
    y: 5.3,
    w: 3.44,
    h: 0.3,
    fontSize: 9.4,
    color: C.soft,
    align: "center",
    margin: 0,
  });
  addArrow(slide, 2.72, 4.74, 3.86, 5.08, C.blue);
  addArrow(slide, 2.72, 5.38, 3.86, 5.22, C.teal);
  addArrow(slide, 2.72, 6.02, 3.86, 5.38, C.gold);
  addCard(slide, 9.3, 4.7, 2.54, 1.2, C.roseSoft, C.rose);
  slide.addText("专家开始审查", {
    x: 9.52,
    y: 5.02,
    w: 2.1,
    h: 0.18,
    fontSize: 12.5,
    bold: true,
    color: C.ink,
    align: "center",
    margin: 0,
  });
  slide.addText("带着上下文、规则和证据分别判断", {
    x: 9.48,
    y: 5.32,
    w: 2.16,
    h: 0.18,
    fontSize: 9.1,
    color: C.soft,
    align: "center",
    margin: 0,
  });
  addArrow(slide, 8.16, 5.3, 9.1, 5.3, C.rose);

  addTag(slide, 0.92, 6.76, 3.6, "一句话记住：文档管规则，Skill 管触发，Tool 管取证", C.blueSoft, C.blue);
  finalize(slide);
}

function expertsSlide() {
  const slide = pptx.addSlide();
  addCanvas(slide, C.bg);
  addHeader(slide, "Experts", "10+ 个领域专家，按问题责任边界分工", "不是角色越多越好，而是让不同类型的问题由对应领域负责。", "03 / 06", C.teal);

  addCard(slide, 0.68, 1.88, 12.0, 1.18, C.white);
  slide.addText("主 Agent 先选专家，再把不同领域的问题分给对应专家，最后统一去重和收敛。", {
    x: 0.98,
    y: 2.08,
    w: 5.8,
    h: 0.2,
    fontSize: 11.5,
    color: C.ink,
    margin: 0,
  });
  addCard(slide, 1.02, 2.42, 2.0, 0.42, C.tealSoft, C.teal);
  addCard(slide, 5.36, 2.35, 2.0, 0.58, C.blueSoft, C.blue);
  addCard(slide, 9.8, 2.42, 2.1, 0.42, C.goldSoft, C.gold);
  slide.addText("业务与架构专家组", { x: 1.02, y: 2.56, w: 2.0, h: 0.12, fontSize: 9.5, bold: true, color: C.ink, align: "center", margin: 0 });
  slide.addText("主 Agent", { x: 5.36, y: 2.54, w: 2.0, h: 0.12, fontSize: 10.8, bold: true, color: C.ink, align: "center", margin: 0 });
  slide.addText("基础设施 / 质量保障", { x: 9.8, y: 2.56, w: 2.1, h: 0.12, fontSize: 9.5, bold: true, color: C.ink, align: "center", margin: 0 });
  addArrow(slide, 3.2, 2.64, 5.2, 2.64, C.blue);
  addArrow(slide, 7.46, 2.64, 9.66, 2.64, C.blue);

  const columns = [
    {
      title: "业务与架构",
      subtitle: "主要回答：逻辑对不对，设计有没有退化。",
      x: 0.7,
      fill: C.tealSoft,
      items: [
        ["业务正确性专家", "看状态流转、边界条件、副作用"],
        ["架构与设计专家", "看聚合边界、依赖方向、职责划分"],
        ["可维护性专家", "看复杂度、重复逻辑、表达清晰度"],
      ],
    },
    {
      title: "基础设施",
      subtitle: "主要回答：技术实现稳不稳、扛不扛得住。",
      x: 4.55,
      fill: C.blueSoft,
      items: [
        ["数据库专家", "看事务、索引、查询性能、批量操作"],
        ["缓存 / Redis 专家", "看缓存一致性、热点放大、失效策略"],
        ["MQ / 性能专家", "看消息可靠性、容量风险、资源使用"],
      ],
    },
    {
      title: "质量保障",
      subtitle: "主要回答：风险有没有被验证住、兜住。",
      x: 8.4,
      fill: C.goldSoft,
      items: [
        ["测试覆盖专家", "看关键路径有没有测试保护"],
        ["安全合规专家", "看权限边界、注入风险、敏感信息"],
        ["前端体验专家", "看可访问性、交互一致性和页面风险"],
      ],
    },
  ];
  columns.forEach((col) => {
    addCard(slide, col.x, 3.28, 3.48, 3.26, C.white);
    addTag(slide, col.x + 0.18, 3.48, 1.3, col.title, col.fill);
    slide.addText(col.subtitle, {
      x: col.x + 0.18,
      y: 3.82,
      w: 3.08,
      h: 0.3,
      fontSize: 9.2,
      color: C.soft,
      margin: 0,
    });
    let y = 4.34;
    col.items.forEach(([title, desc]) => {
      addCard(slide, col.x + 0.16, y, 3.12, 0.58, col.fill, col.fill);
      slide.addText(title, {
        x: col.x + 0.3,
        y: y + 0.11,
        w: 1.15,
        h: 0.14,
        fontSize: 9.4,
        bold: true,
        color: C.ink,
        margin: 0,
      });
      slide.addText(desc, {
        x: col.x + 1.52,
        y: y + 0.11,
        w: 1.54,
        h: 0.28,
        fontSize: 8.5,
        color: C.soft,
        margin: 0,
      });
      y += 0.72;
    });
  });
  addTag(slide, 3.72, 6.86, 5.86, "一个典型复杂 MR，往往会同时拉起业务正确性、架构、数据库和性能几个专家分别审查。", C.tealSoft, C.teal);
  finalize(slide);
}

function compareSlide() {
  const slide = pptx.addSlide();
  addCanvas(slide);
  addHeader(slide, "Multi Agent", "为什么复杂代码审查需要多 Agent 协作？", "多 Agent 的核心价值，不只是管理上下文窗口，而是把复杂审查从“一个模型泛泛地看”变成“多领域分工协作地看”。", "04 / 06", C.orange);

  addCard(slide, 0.74, 1.92, 5.86, 4.58, C.roseSoft, C.rose);
  addCard(slide, 6.72, 1.92, 5.86, 4.58, C.tealSoft, C.teal);
  slide.addText("单 Agent 的问题", {
    x: 1.02, y: 2.18, w: 2.2, h: 0.18, fontSize: 14, bold: true, color: C.ink, margin: 0,
  });
  slide.addText("多 Agent 的优势", {
    x: 7.0, y: 2.18, w: 2.2, h: 0.18, fontSize: 14, bold: true, color: C.ink, margin: 0,
  });
  addBulletList(slide, 1.0, 2.58, 5.2, [
    "所有问题混在一个上下文里，业务、架构、数据库、性能很容易互相稀释。",
    "模型容易用同一套判断方式去看完全不同类型的问题。",
    "输出像“什么都看了”，但深度和稳定性不够一致。",
    "上下文窗口不是唯一问题，专业分工不清才是更大的问题。",
  ], C.ink, 10.2, C.rose);
  addBulletList(slide, 7.0, 2.58, 5.2, [
    "每类问题由对应专家处理，分工边界更清楚。",
    "每个专家只拿和自己职责相关的文档、规则和代码证据。",
    "不同领域可以并行补证据，不必互相拖累。",
    "最后还能由 judge、阈值和治理逻辑统一收敛结果。",
  ], C.ink, 10.2, C.teal);
  addTag(slide, 1.42, 6.82, 10.44, "关键点：多 Agent 的价值，不只是上下文窗口管理，而是专业分工、证据隔离和结果收敛。", C.blueSoft, C.blue);
  finalize(slide);
}

function frameworkSlide() {
  const slide = pptx.addSlide();
  addCanvas(slide, C.bg);
  addHeader(slide, "Framework", "为什么选择 LangGraph 作为流程编排框架？", "我们面对的不是简单对话，而是一条需要状态管理、流程编排和治理能力的复杂审查链路。", "05 / 06", C.blue);

  addCard(slide, 0.66, 1.88, 12.0, 1.26, C.white);
  slide.addText("我们真正要跑的是这样一条链", {
    x: 0.96, y: 2.08, w: 2.8, h: 0.18, fontSize: 13, bold: true, color: C.ink, margin: 0,
  });
  const nodes = [
    ["选专家", 0.98, C.blueSoft],
    ["补证据", 3.28, C.tealSoft],
    ["并行审查", 5.58, C.goldSoft],
    ["结果收敛", 7.96, C.roseSoft],
    ["人工介入", 10.32, C.blueSoft],
  ];
  nodes.forEach(([label, x, fill], idx) => {
    addCard(slide, x, 2.48, 1.72, 0.42, fill, fill);
    slide.addText(label, {
      x, y: 2.61, w: 1.72, h: 0.12, fontSize: 9.4, bold: true, color: C.ink, align: "center", margin: 0,
    });
    if (idx < nodes.length - 1) addArrow(slide, x + 1.82, 2.69, x + 2.18, 2.69, C.blue);
  });

  addCard(slide, 0.66, 3.56, 12.0, 2.74, C.white);
  const headers = ["我们真正需要什么", "对其他方案的判断", "为什么最后选 LangGraph"];
  const bodies = [
    [
      "多专家协作、状态流转、失败重试、人工门禁、过程回放和治理观测，都要放进同一条链路里。",
      "专家选择、补证据、judge 和阈值治理必须清楚串起来。",
      "每次执行的状态都要保留下来，方便恢复和追溯。",
    ],
    [
      "直接调模型 API 适合起步快，但流程和状态都要自己在外面补。",
      "对话式 Agent 更适合“多个角色一起说话”，对多分支流程表达通常不够直观。",
      "能做执行，不等于能做成可治理、可回放的工程系统。",
    ],
    [
      "节点和边就是流程本身，天然适合多阶段、多分支执行逻辑。",
      "状态是第一公民，更适合长流程、长任务和需要恢复的场景。",
      "更容易把复杂审查流程做成一条可观测、可治理的工程链。",
    ],
  ];
  [0.92, 4.38, 7.84].forEach((x, i) => {
    addCard(slide, x, 3.88, 3.12, 2.08, i === 2 ? C.blueSoft : C.bg, i === 2 ? C.blue : C.line);
    slide.addText(headers[i], {
      x: x + 0.18, y: 4.08, w: 2.7, h: 0.18, fontSize: 11.5, bold: true, color: C.ink, margin: 0,
    });
    addBulletList(slide, x + 0.18, 4.44, 2.74, bodies[i], C.ink, 8.7, i === 2 ? C.blue : C.soft);
  });
  addTag(slide, 1.2, 6.78, 10.8, "选择 LangGraph，不是因为它更流行，而是因为这类多专家代码审查本质上就是一条需要状态管理和治理能力的复杂流程。", C.blueSoft, C.blue);
  finalize(slide);
}

function qualitySlide() {
  const slide = pptx.addSlide();
  addCanvas(slide);
  addHeader(slide, "Quality", "当前质量水平：已能在真实 MR 上发现高价值问题，但稳定性仍需提升", "这套工具还在开发期，所以这里不强调效果数字，重点讲现在做到哪一步、还差什么。", "06 / 06", C.teal);

  addCard(slide, 0.7, 1.9, 5.92, 3.86, C.white);
  slide.addText("现在已经看得见的价值", {
    x: 0.98, y: 2.14, w: 2.6, h: 0.18, fontSize: 13, bold: true, color: C.ink, margin: 0,
  });
  slide.addText("真实案例：订单 / 事件 / 批量读取这类复杂改动", {
    x: 0.98, y: 2.44, w: 3.8, h: 0.16, fontSize: 9.6, color: C.soft, margin: 0,
  });
  const caseSteps = [
    ["PR 改动", "同时改了聚合创建、事件发布顺序和批量读取逻辑"],
    ["系统命中", "业务正确性、架构、数据库几个专家分别给出发现"],
    ["结论价值", "抓到的不是表面代码味道，而是更容易漏掉的高风险问题"],
  ];
  let y = 2.86;
  caseSteps.forEach(([title, body], idx) => {
    addCard(slide, 1.0, y, 5.3, 0.72, idx === 0 ? C.blueSoft : idx === 1 ? C.tealSoft : C.goldSoft, C.line);
    addTag(slide, 1.16, y + 0.18, 0.38, String(idx + 1), C.white, C.ink);
    slide.addText(title, {
      x: 1.68, y: y + 0.15, w: 1.0, h: 0.14, fontSize: 10, bold: true, color: C.ink, margin: 0,
    });
    slide.addText(body, {
      x: 2.82, y: y + 0.15, w: 3.08, h: 0.28, fontSize: 8.7, color: C.soft, margin: 0,
    });
    y += 0.88;
  });

  addCard(slide, 6.78, 1.9, 5.86, 3.86, C.white);
  slide.addText("接下来最该补的三件事", {
    x: 7.06, y: 2.14, w: 2.8, h: 0.18, fontSize: 13, bold: true, color: C.ink, margin: 0,
  });
  const roadmap = [
    ["先把流程跑稳", "先解决大任务、内网调用和 Windows 环境问题", C.blueSoft],
    ["再把误报压下来", "继续优化规则筛选、阈值治理和上下文组装", C.tealSoft],
    ["把关键专家做深", "重点增强架构、业务正确性、数据库和性能专家", C.goldSoft],
  ];
  y = 2.72;
  roadmap.forEach(([title, body, fill], idx) => {
    addCard(slide, 7.02, y, 5.36, 0.82, fill, fill);
    addTag(slide, 7.18, y + 0.22, 0.42, String(idx + 1), C.white, C.ink);
    slide.addText(title, {
      x: 7.76, y: y + 0.16, w: 1.7, h: 0.14, fontSize: 10, bold: true, color: C.ink, margin: 0,
    });
    slide.addText(body, {
      x: 9.66, y: y + 0.16, w: 2.42, h: 0.28, fontSize: 8.6, color: C.soft, margin: 0,
    });
    y += 1.0;
  });
  addTag(slide, 1.12, 6.2, 11.0, "这套系统已经证明方向成立。下一阶段的重点，不是继续堆功能，而是把这份价值更稳定、更可信地交付出来。", C.tealSoft, C.teal);
  finalize(slide);
}

coverSlide();
extensibilitySlide();
expertsSlide();
compareSlide();
frameworkSlide();
qualitySlide();

(async () => {
  await pptx.writeFile({ fileName: OUT_FILE });
  console.log(`Wrote ${OUT_FILE}`);
})();
