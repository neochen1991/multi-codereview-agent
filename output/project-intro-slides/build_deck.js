const fs = require("fs");
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
pptx.subject = "Multi Code Review Agent";
pptx.title = "多专家代码审核系统介绍";
pptx.lang = "zh-CN";
pptx.theme = {
  headFontFace: "Aptos",
  bodyFontFace: "Aptos",
  lang: "zh-CN",
};

const OUT_DIR = __dirname;
const OUT_FILE = path.join(OUT_DIR, "multi-code-review-agent-slides.pptx");

const C = {
  ink: "17324D",
  inkSoft: "47617A",
  accent: "F97316",
  accentSoft: "FFF1E8",
  teal: "0F766E",
  tealSoft: "E7F8F6",
  sky: "0EA5E9",
  skySoft: "EAF7FF",
  gold: "D97706",
  goldSoft: "FFF7E6",
  rose: "E11D48",
  roseSoft: "FFF0F3",
  slate: "EEF2F6",
  line: "D7E0EA",
  white: "FFFFFF",
  canvas: "F6F3EE",
  canvasAlt: "FBFAF8",
  darkPanel: "10253A",
  darkPanel2: "173B59",
  mint: "14B8A6",
  mintSoft: "E8FCF7",
  plum: "7C3AED",
  plumSoft: "F3EEFF",
};

function addCanvas(slide, color = C.canvasAlt) {
  slide.background = { color: C.canvasAlt };
  slide.addShape(pptx.ShapeType.rect, {
    x: 0,
    y: 0,
    w: 13.333,
    h: 7.5,
    line: { color: C.canvasAlt, transparency: 100 },
    fill: { color: C.canvasAlt },
  });
}

function addBackdropShapes(slide) {
  slide.addShape(pptx.ShapeType.rect, {
    x: 12.92,
    y: 0.58,
    w: 0.08,
    h: 6.34,
    line: { color: "E8EDF3", transparency: 100 },
    fill: { color: "E8EDF3", transparency: 0 },
  });
}

function addTopBand(slide, label, title, subtitle, accent = C.accent) {
  slide.addShape(pptx.ShapeType.rect, {
    x: 0,
    y: 0,
    w: 13.333,
    h: 0.18,
    line: { color: C.darkPanel, transparency: 100 },
    fill: { color: C.darkPanel },
  });
  slide.addShape(pptx.ShapeType.roundRect, {
    x: 0.58,
    y: 0.54,
    w: 1.18,
    h: 0.26,
    rectRadius: 0.06,
    line: { color: accent, transparency: 100 },
    fill: { color: accent },
  });
  slide.addText(label, {
    x: 0.64,
    y: 0.59,
    w: 1.06,
    h: 0.12,
    fontFace: "Aptos",
    fontSize: 8.2,
    color: C.white,
    bold: true,
    align: "center",
    margin: 0,
  });
  slide.addText(title, {
    x: 0.58,
    y: 0.94,
    w: 9.0,
    h: 0.4,
    fontFace: "Aptos",
    fontSize: 22,
    bold: true,
    color: C.ink,
    margin: 0,
  });
  if (subtitle) {
    slide.addText(subtitle, {
      x: 0.58,
      y: 1.44,
      w: 8.9,
      h: 0.22,
      fontFace: "Aptos",
      fontSize: 9.8,
      color: C.inkSoft,
      margin: 0,
    });
  }
}

function addRoundedCard(slide, x, y, w, h, opts = {}) {
  slide.addShape(pptx.ShapeType.roundRect, {
    x,
    y,
    w,
    h,
    rectRadius: 0.08,
    line: { color: opts.lineColor || C.line, pt: opts.linePt || 1 },
    fill: { color: opts.fill || C.white },
    shadow: opts.shadow
      ? { type: "outer", color: "D9E2EC", blur: 1, angle: 45, distance: 0.6, opacity: 0.12 }
      : undefined,
  });
}

function addPill(slide, x, y, w, text, fill, color = C.ink) {
  slide.addShape(pptx.ShapeType.roundRect, {
    x,
    y,
    w,
    h: 0.24,
    rectRadius: 0.08,
    line: { color: fill, transparency: 100 },
    fill: { color: fill },
  });
  slide.addText(text, {
    x: x + 0.08,
    y: y + 0.035,
    w: w - 0.16,
    h: 0.16,
    fontSize: 8,
    color,
    bold: true,
    align: "center",
    margin: 0,
  });
}

function addMetric(slide, x, y, w, title, value, accentFill) {
  addRoundedCard(slide, x, y, w, 1.15, { fill: C.white, lineColor: C.line, shadow: true });
  slide.addShape(pptx.ShapeType.roundRect, {
    x: x + 0.18,
    y: y + 0.18,
    w: 0.76,
    h: 0.18,
    rectRadius: 0.05,
    line: { color: accentFill, transparency: 100 },
    fill: { color: accentFill },
  });
  slide.addText(title, {
    x: x + 0.18,
    y: y + 0.41,
    w: w - 0.36,
    h: 0.18,
    fontSize: 9,
    color: C.inkSoft,
    bold: true,
    margin: 0,
  });
  slide.addText(value, {
    x: x + 0.18,
    y: y + 0.62,
    w: w - 0.36,
    h: 0.28,
    fontSize: 18.5,
    color: C.ink,
    bold: true,
    margin: 0,
  });
}

function resolveAccentColor(fill) {
  const mapping = {
    [C.accentSoft]: C.accent,
    [C.skySoft]: C.sky,
    [C.tealSoft]: C.teal,
    [C.goldSoft]: C.gold,
    [C.roseSoft]: C.rose,
    [C.mintSoft]: C.mint,
    [C.plumSoft]: C.plum,
    [C.slate]: C.inkSoft,
  };
  return mapping[fill] || null;
}

function addNode(slide, x, y, w, h, title, subtitle, opts = {}) {
  const requestedFill = opts.fill || C.white;
  const derivedAccent = opts.accent || resolveAccentColor(requestedFill);
  const normalizedFill = derivedAccent ? C.white : requestedFill;
  addRoundedCard(slide, x, y, w, h, {
    fill: normalizedFill,
    lineColor: opts.lineColor || C.line,
    shadow: opts.shadow !== false,
  });
  if (derivedAccent && !opts.badge) {
    slide.addShape(pptx.ShapeType.roundRect, {
      x: x + 0.14,
      y: y + 0.11,
      w: Math.min(0.9, w - 0.28),
      h: 0.04,
      rectRadius: 0.03,
      line: { color: derivedAccent, transparency: 100 },
      fill: { color: derivedAccent },
    });
  }
  if (opts.badge) {
    addPill(slide, x + 0.14, y + 0.12, Math.min(1.55, w - 0.28), opts.badge, opts.badgeFill || C.slate, opts.badgeColor || C.ink);
  }
  slide.addText(title, {
    x: x + 0.14,
    y: y + (opts.badge ? 0.48 : derivedAccent ? 0.28 : 0.18),
    w: w - 0.28,
    h: 0.22,
    fontSize: opts.titleSize || 11.5,
    color: opts.titleColor || C.ink,
    bold: true,
    margin: 0,
  });
  if (subtitle) {
    slide.addText(subtitle, {
      x: x + 0.14,
      y: y + (opts.badge ? 0.74 : derivedAccent ? 0.54 : 0.46),
      w: w - 0.28,
      h: h - (opts.badge ? 0.84 : derivedAccent ? 0.64 : 0.54),
      fontSize: opts.bodySize || 8.2,
      color: opts.bodyColor || C.inkSoft,
      valign: "mid",
      margin: 0,
    });
  }
}

function addArrow(slide, x, y, w, text, color = C.inkSoft) {
  slide.addShape(pptx.ShapeType.line, {
    x,
    y: y + 0.08,
    w,
    h: 0,
    line: { color, pt: 1.4, endArrowType: "triangle" },
  });
  if (text) {
    slide.addText(text, {
      x,
      y: y - 0.08,
      w,
      h: 0.14,
      fontSize: 7.8,
      color,
      bold: true,
      margin: 0,
      align: "center",
    });
  }
}

function addBulletList(slide, x, y, w, items, color = C.inkSoft, fontSize = 10) {
  const runs = [];
  items.forEach((item, idx) => {
    runs.push({
      text: item,
      options: { bullet: { indent: 12 }, hanging: 3, breakLine: idx !== items.length - 1 },
    });
  });
  slide.addText(runs, {
    x,
    y,
    w,
    h: items.length * 0.34 + 0.1,
    fontSize,
    color,
    breakLine: false,
    margin: 0,
    paraSpaceAfterPt: 5,
  });
}

function finalizeSlide(slide) {
  warnIfSlideHasOverlaps(slide, pptx);
  warnIfSlideElementsOutOfBounds(slide, pptx);
}

function addCoverSlide() {
  const slide = pptx.addSlide();
  addCanvas(slide, C.canvasAlt);
  addBackdropShapes(slide);
  slide.addShape(pptx.ShapeType.roundRect, {
    x: 0.62,
    y: 0.65,
    w: 12.05,
    h: 6.15,
    rectRadius: 0.12,
    line: { color: C.line, pt: 1 },
    fill: { color: C.white },
  });
  slide.addShape(pptx.ShapeType.roundRect, {
    x: 6.62,
    y: 0.95,
    w: 5.35,
    h: 5.55,
    rectRadius: 0.12,
    line: { color: C.darkPanel, pt: 1.2 },
    fill: { color: C.darkPanel },
  });
  addPill(slide, 0.96, 0.9, 1.8, "代码审查平台", C.accentSoft, C.accent);
  slide.addText("多专家代码审核系统", {
    x: 0.96,
    y: 1.32,
    w: 5.2,
    h: 0.48,
    fontSize: 28,
    bold: true,
    color: C.ink,
    margin: 0,
  });
  slide.addText("从真实 PR diff 到专家协作、规范审视、技能扩展与人工裁决", {
    x: 0.98,
    y: 1.9,
    w: 5.3,
    h: 0.4,
    fontSize: 12.5,
    color: C.inkSoft,
    margin: 0,
  });
  addMetric(slide, 0.98, 2.6, 1.72, "多专家", "11", C.accentSoft);
  addMetric(slide, 2.86, 2.6, 1.72, "扩展能力", "Skill + Tool", C.skySoft);
  addMetric(slide, 4.74, 2.6, 1.72, "评审流", "实时可追踪", C.tealSoft);

  slide.addText("平台故事线", {
    x: 0.98,
    y: 4.15,
    w: 1.5,
    h: 0.2,
    fontSize: 11,
    bold: true,
    color: C.ink,
    margin: 0,
  });
  addBulletList(
    slide,
    1.0,
    4.42,
    4.9,
    [
      "为什么传统 CR 只能给线索，难以稳定给出高质量结论",
      "系统如何把 Expert、Skill、Tool 与源码仓上下文编排成一条审查链",
      "如何用设计文档一致性检查证明扩展机制真的可用",
    ],
    C.inkSoft,
    10
  );

  slide.addText("系统全景", {
    x: 6.95,
    y: 1.18,
    w: 1.5,
    h: 0.18,
    fontSize: 11,
    color: "DAE6F2",
    bold: true,
    margin: 0,
  });
  addNode(slide, 6.95, 1.62, 1.95, 0.88, "工作台", "启动 · 过程 · 结果", {
    fill: "1F486C",
    lineColor: "3A6A93",
    titleColor: C.white,
    bodyColor: "D6E4F0",
    shadow: false,
  });
  addNode(slide, 9.55, 1.62, 1.95, 0.88, "后端 Runtime", "编排 · LLM · 路由", {
    fill: "1F486C",
    lineColor: "3A6A93",
    titleColor: C.white,
    bodyColor: "D6E4F0",
    shadow: false,
  });
  addArrow(slide, 8.96, 2.0, 0.42, "");
  addNode(slide, 6.95, 3.0, 1.95, 0.88, "主 Agent", "按 hunk / 符号 / repo context 派工", {
    fill: "183A56",
    lineColor: "2B5C7E",
    titleColor: C.white,
    bodyColor: "D6E4F0",
    shadow: false,
  });
  addNode(slide, 9.55, 3.0, 1.95, 0.88, "专家 Agent", "规范文档 + 领域结论", {
    fill: "183A56",
    lineColor: "2B5C7E",
    titleColor: C.white,
    bodyColor: "D6E4F0",
    shadow: false,
  });
  addArrow(slide, 8.98, 3.38, 0.5, "");
  addNode(slide, 6.95, 4.38, 1.95, 0.88, "Skills", "能力包：何时触发、依赖什么", {
    fill: "294B33",
    lineColor: "3A6E4D",
    titleColor: C.white,
    bodyColor: "DBF4E7",
    shadow: false,
  });
  addNode(slide, 9.55, 4.38, 1.95, 0.88, "Tools", "结构化取证与比对插件", {
    fill: "294B33",
    lineColor: "3A6E4D",
    titleColor: C.white,
    bodyColor: "DBF4E7",
    shadow: false,
  });
  addArrow(slide, 8.98, 4.76, 0.5, "");
  addNode(slide, 8.25, 5.55, 1.95, 0.72, "可追踪结果", "Finding / Issue / Human Gate", {
    fill: "5B2841",
    lineColor: "8D4A6C",
    titleColor: C.white,
    bodyColor: "F2DBE6",
    shadow: false,
    titleSize: 10.8,
    bodySize: 8.1,
  });

  finalizeSlide(slide);
}

function addWhySlide() {
  const slide = pptx.addSlide();
  addCanvas(slide);
  addBackdropShapes(slide);
  addTopBand(slide, "01 WHY", "为什么要做这套系统", "不是再造一个静态 diff 查看器，而是把“审查过程”显性化。", C.accent);

  addNode(slide, 0.65, 1.75, 3.9, 4.8, "传统代码审查的典型断点", "", {
    fill: C.white,
    lineColor: C.line,
    shadow: true,
  });
  const painY = [2.25, 3.15, 4.05, 4.95, 5.85];
  const painTexts = [
    ["只看 diff", "缺少目标分支上下文，容易误判 import、字段和链路影响。"],
    ["人工 review 不可追踪", "结论来自经验，但缺少结构化证据和过程可视化。"],
    ["专家知识不可复用", "规范、知识文档和扩展能力散落在个人经验中。"],
    ["复杂 PR 很难收敛", "安全、数据库、性能、正确性问题混在一起，很难稳定分工。"],
    ["结果不可治理", "没有 skill / tool 扩展位，新增专业检查只能改主流程源码。"],
  ];
  painTexts.forEach((pair, index) => {
    slide.addShape(pptx.ShapeType.roundRect, {
      x: 0.92,
      y: painY[index],
      w: 3.35,
      h: 0.58,
      rectRadius: 0.06,
      line: { color: C.line, pt: 1 },
      fill: { color: index % 2 === 0 ? C.canvasAlt : C.slate },
    });
    slide.addText(pair[0], {
      x: 1.1,
      y: painY[index] + 0.11,
      w: 0.9,
      h: 0.16,
      fontSize: 10.5,
      bold: true,
      color: C.ink,
      margin: 0,
    });
    slide.addText(pair[1], {
      x: 2.22,
      y: painY[index] + 0.11,
      w: 1.82,
      h: 0.26,
      fontSize: 8.4,
      color: C.inkSoft,
      margin: 0,
    });
  });

  addNode(slide, 5.0, 1.75, 7.55, 4.8, "这套系统如何回答这些问题", "", {
    fill: C.white,
    lineColor: C.line,
    shadow: true,
  });
  const tiles = [
    [5.35, 2.2, 2.18, 1.35, "真实 diff + 源码仓", "GitHub / GitLab patch + repo context 检索，补齐上下文。", C.skySoft],
    [7.72, 2.2, 2.18, 1.35, "主 Agent 编排", "按 hunk、符号、上下文派工，而不是只按文件名猜。", C.accentSoft],
    [10.08, 2.2, 2.12, 1.35, "专家规范审视", "每个专家都带规范文档、参考文档和边界约束。", C.tealSoft],
    [5.35, 4.0, 2.18, 1.35, "Skill + Tool 扩展", "能力插件只放 extensions，主源码保持稳定。", C.goldSoft],
    [7.72, 4.0, 2.18, 1.35, "过程实时可见", "主 Agent、专家、tool 调用、裁决都能在工作台里追踪。", C.roseSoft],
    [10.08, 4.0, 2.12, 1.35, "人工裁决闭环", "对不确定风险进入 human gate，而不是装作系统已确定。", C.slate],
  ];
  tiles.forEach(([x, y, w, h, title, body, fill]) =>
    addNode(slide, x, y, w, h, title, body, { fill, lineColor: C.line, shadow: false, titleSize: 10.8, bodySize: 8.4 })
  );
  finalizeSlide(slide);
}

function addArchitectureSlide() {
  const slide = pptx.addSlide();
  addCanvas(slide, C.canvasAlt);
  addBackdropShapes(slide);
  addTopBand(slide, "02 SYSTEM", "系统总体架构", "前端工作台、后端 runtime、扩展层与存储层之间的职责拆分。", C.sky);

  const layers = [
    { y: 1.7, h: 1.08, title: "用户与工作台", fill: C.skySoft },
    { y: 3.0, h: 1.28, title: "后端应用与运行时", fill: C.accentSoft },
    { y: 4.6, h: 1.15, title: "运行时扩展层", fill: C.tealSoft },
    { y: 6.0, h: 0.85, title: "持久化与配置", fill: C.goldSoft },
  ];
  layers.forEach((layer) => {
    slide.addShape(pptx.ShapeType.roundRect, {
      x: 0.68,
      y: layer.y,
      w: 12.0,
      h: layer.h,
      rectRadius: 0.06,
      line: { color: C.line, pt: 1 },
      fill: { color: layer.fill },
    });
    slide.addText(layer.title, {
      x: 0.88,
      y: layer.y + 0.08,
      w: 2.0,
      h: 0.18,
      fontSize: 10.5,
      bold: true,
      color: C.ink,
      margin: 0,
    });
  });

  addNode(slide, 1.0, 2.0, 2.25, 0.56, "React 工作台", "Home / Review / History");
  addNode(slide, 3.52, 2.0, 2.55, 0.56, "审核过程页", "对话流 · Diff · Findings");
  addNode(slide, 6.36, 2.0, 2.28, 0.56, "专家中心", "规范、文档、运行时工具");
  addNode(slide, 8.92, 2.0, 2.9, 0.56, "知识库 / 设置 / 历史", "治理配置、长文档与审核结果");

  addNode(slide, 1.0, 3.35, 2.0, 0.7, "FastAPI API", "Review / Experts / Settings", {
    fill: C.white,
    lineColor: C.line,
  });
  addNode(slide, 3.28, 3.35, 2.35, 0.7, "ReviewService", "创建 review、读配置、绑定设计文档");
  addNode(slide, 5.92, 3.35, 2.28, 0.7, "ReviewRunner", "主 Agent / 专家 / Judge / Human Gate");
  addNode(slide, 8.5, 3.35, 2.2, 0.7, "PlatformAdapter", "GitHub / GitLab / CodeHub diff");
  addNode(slide, 10.98, 3.35, 1.3, 0.7, "SSE", "实时事件流");
  addArrow(slide, 3.04, 3.62, 0.18, "");
  addArrow(slide, 5.64, 3.62, 0.18, "");
  addArrow(slide, 8.22, 3.62, 0.18, "");
  addArrow(slide, 10.74, 3.62, 0.18, "");

  addNode(slide, 1.15, 4.9, 2.18, 0.56, "Experts", "架构 / 正确性 / 安全 / DB");
  addNode(slide, 3.55, 4.9, 2.18, 0.56, "Skills 扩展", "", {
    fill: C.goldSoft,
    lineColor: C.line,
    shadow: false,
  });
  addNode(slide, 5.95, 4.9, 2.18, 0.56, "Tools 插件", "", {
    fill: C.tealSoft,
    lineColor: C.line,
    shadow: false,
  });
  addNode(slide, 8.35, 4.9, 2.18, 0.56, "Repo Context", "源码仓检索、符号与引用");
  addNode(slide, 10.75, 4.9, 1.55, 0.56, "LLM", "实时调用");

  addNode(slide, 1.2, 6.26, 2.25, 0.42, "storage/reviews", "review / finding / issue / event", { fill: C.white, shadow: false });
  addNode(slide, 3.72, 6.26, 2.25, 0.42, "storage/experts", "专家资料与绑定文档", { fill: C.white, shadow: false });
  addNode(slide, 6.24, 6.26, 2.25, 0.42, "config.json", "模型、Git token、端口与网络", { fill: C.white, shadow: false });
  addNode(slide, 8.76, 6.26, 3.0, 0.42, "extensions", "skills / tools 只改扩展目录，不碰主源码", { fill: C.white, shadow: false });
  finalizeSlide(slide);
}

function addHouseArchitectureSlide() {
  const slide = pptx.addSlide();
  addCanvas(slide);
  addBackdropShapes(slide);
  addTopBand(slide, "03 OVERVIEW", "总体架构图：像房子一样看懂整套系统", "用“屋顶-中层-底座”表达用户入口、智能体运行层和基础设施底座。", C.mint);
  slide.addShape(pptx.ShapeType.line, {
    x: 2.0,
    y: 2.68,
    w: 3.4,
    h: -0.95,
    line: { color: C.ink, pt: 2.2 },
  });
  slide.addShape(pptx.ShapeType.line, {
    x: 8.0,
    y: 1.73,
    w: 3.4,
    h: 0.95,
    line: { color: C.ink, pt: 2.2 },
  });
  slide.addShape(pptx.ShapeType.line, {
    x: 3.05,
    y: 2.68,
    w: 7.3,
    h: 0,
    line: { color: C.ink, pt: 2.2 },
  });
  addPill(slide, 5.15, 1.78, 2.1, "屋顶：用户入口层", C.darkPanel, C.white);

  addNode(slide, 1.62, 2.98, 2.72, 0.92, "首页 / 历史 / 设置", "系统入口、状态总览、治理入口", {
    fill: C.skySoft,
    shadow: true,
  });
  addNode(slide, 4.55, 2.98, 2.72, 0.92, "审核工作台", "概览启动 · 过程追踪 · 结论裁决", {
    fill: C.accentSoft,
    shadow: true,
  });
  addNode(slide, 7.48, 2.98, 2.72, 0.92, "专家中心 / 知识库", "规范文档、专家资料、长期知识与扩展治理", {
    fill: C.tealSoft,
    shadow: true,
  });

  addRoundedCard(slide, 1.22, 4.18, 10.92, 1.78, { fill: C.white, lineColor: C.line, shadow: true });
  addPill(slide, 1.48, 4.42, 2.0, "中层：智能体运行层", C.plumSoft, C.plum);
  slide.addShape(pptx.ShapeType.line, {
    x: 6.66,
    y: 4.68,
    w: 0,
    h: 1.05,
    line: { color: C.line, pt: 1.1, dash: "dash" },
  });
  slide.addText("智能判断", {
    x: 2.3,
    y: 4.78,
    w: 1.4,
    h: 0.14,
    fontSize: 8.4,
    color: C.inkSoft,
    bold: true,
    margin: 0,
  });
  slide.addText("能力扩展", {
    x: 7.24,
    y: 4.78,
    w: 1.4,
    h: 0.14,
    fontSize: 8.4,
    color: C.inkSoft,
    bold: true,
    margin: 0,
  });
  addNode(slide, 1.52, 4.98, 2.18, 0.72, "主 Agent", "理解 diff、挑专家、控制节奏", {
    fill: C.accentSoft,
    shadow: false,
  });
  addNode(slide, 4.1, 4.98, 2.18, 0.72, "专家 Agent", "按边界输出结论，而不是泛化评论", {
    fill: C.skySoft,
    shadow: false,
  });
  addNode(slide, 6.98, 4.98, 2.08, 0.72, "Skills", "定义何时触发、要求输出什么", {
    fill: C.goldSoft,
    shadow: false,
  });
  addNode(slide, 9.42, 4.98, 2.08, 0.72, "Tools", "repo context、diff、设计文档结构化", {
    fill: C.tealSoft,
    shadow: false,
  });
  addArrow(slide, 3.78, 5.3, 0.18, "");
  addArrow(slide, 6.48, 5.3, 0.18, "");
  addArrow(slide, 9.12, 5.3, 0.18, "");

  addPill(slide, 5.1, 6.04, 2.0, "底座：基础设施层", C.darkPanel, C.white);
  addNode(slide, 1.45, 6.28, 3.0, 0.72, "真实上下文底座", "真实 patch、源码仓、设计文档输入", {
    fill: C.white,
    shadow: true,
    accent: C.sky,
  });
  addNode(slide, 5.12, 6.28, 2.48, 0.72, "可扩展底座", "config.json + extensions + plugin 机制", {
    fill: C.white,
    shadow: true,
    accent: C.gold,
  });
  addNode(slide, 8.28, 6.28, 2.95, 0.72, "可治理底座", "storage、日志、历史、人工裁决与回放", {
    fill: C.white,
    shadow: true,
    accent: C.teal,
  });

  finalizeSlide(slide);
}

function addLifecycleSlide() {
  const slide = pptx.addSlide();
  addCanvas(slide);
  addBackdropShapes(slide);
  addTopBand(slide, "03 FLOW", "一次审核任务如何跑完整条链", "从输入 PR 到问题清单，核心是“取真数据、派对专家、给证据、再裁决”。", C.teal);

  const steps = [
    ["1", "用户输入与绑定", "PR 链接 + 模式 + 专家 + design spec"],
    ["2", "拉取真实 diff", "PlatformAdapter 拉 patch / diff 并规范化 changed_files"],
    ["3", "主 Agent 派工", "按 hunk、symbol、repo context 路由专家"],
    ["4", "专家执行", "激活 skill，展开 tool，读取 repo context 与规范文档"],
    ["5", "收敛与裁决", "judge 合并 finding，必要时进入 human gate"],
    ["6", "结果展示", "过程页实时流 + 结果页问题清单 + 人工裁决"],
  ];
  const fills = [C.skySoft, C.accentSoft, C.goldSoft, C.tealSoft, C.roseSoft, C.slate];
  steps.forEach((step, idx) => {
    const y = 1.78 + idx * 0.78;
    slide.addShape(pptx.ShapeType.roundRect, {
      x: 0.92,
      y,
      w: 11.5,
      h: 0.56,
      rectRadius: 0.06,
      line: { color: C.line, pt: 1 },
      fill: { color: fills[idx] },
    });
    slide.addShape(pptx.ShapeType.roundRect, {
      x: 1.12,
      y: y + 0.11,
      w: 0.42,
      h: 0.32,
      rectRadius: 0.06,
      line: { color: C.ink, transparency: 100 },
      fill: { color: C.darkPanel },
    });
    slide.addText(step[0], {
      x: 1.12,
      y: y + 0.155,
      w: 0.42,
      h: 0.12,
      fontSize: 9,
      bold: true,
      color: C.white,
      align: "center",
      margin: 0,
    });
    slide.addText(step[1], {
      x: 1.72,
      y: y + 0.12,
      w: 2.4,
      h: 0.16,
      fontSize: 11,
      bold: true,
      color: C.ink,
      margin: 0,
    });
    slide.addText(step[2], {
      x: 4.25,
      y: y + 0.12,
      w: 6.9,
      h: 0.18,
      fontSize: 8.8,
      color: C.inkSoft,
      margin: 0,
    });
  });

  addNode(slide, 9.2, 6.6, 2.4, 0.44, "关键原则", "真实 patch、真实 repo、实时证据流", {
    fill: C.darkPanel,
    lineColor: C.darkPanel,
    titleColor: C.white,
    bodyColor: "D9E4EF",
    shadow: false,
  });
  finalizeSlide(slide);
}

function addCapabilitySlide() {
  const slide = pptx.addSlide();
  addCanvas(slide, C.canvasAlt);
  addBackdropShapes(slide);
  addTopBand(slide, "04 CAPABILITY", "专家、Skill、Tool 是怎么协同的", "Skill 是能力包，Tool 是执行单元，Runtime 决定何时激活。", C.gold);

  addNode(slide, 0.9, 1.9, 2.2, 1.2, "Expert", "带领域规范、长期知识文档和边界约束", {
    fill: C.white,
    lineColor: C.line,
    badge: "Agent",
    badgeFill: C.skySoft,
  });
  addNode(slide, 3.55, 1.9, 2.4, 1.2, "Skill", "SKILL.md 规定触发条件、依赖工具、输出契约", {
    fill: C.white,
    lineColor: C.line,
    badge: "extension",
    badgeFill: C.goldSoft,
    badgeColor: C.gold,
  });
  addNode(slide, 6.4, 1.9, 2.35, 1.2, "Tool", "run.py 通过 stdin/stdout JSON 返回结构化证据", {
    fill: C.white,
    lineColor: C.line,
    badge: "plugin",
    badgeFill: C.tealSoft,
    badgeColor: C.teal,
  });
  addNode(slide, 9.2, 1.9, 2.35, 1.2, "Finding", "最终审查结论写回 finding / issue / report", {
    fill: C.white,
    lineColor: C.line,
    badge: "result",
    badgeFill: C.roseSoft,
    badgeColor: C.rose,
  });
  addArrow(slide, 3.14, 2.46, 0.24, "");
  addArrow(slide, 6.04, 2.46, 0.2, "");
  addArrow(slide, 8.92, 2.46, 0.18, "");

  addRoundedCard(slide, 0.9, 3.7, 5.65, 2.42, { fill: C.white, lineColor: C.line, shadow: true });
  slide.addText("Skill 激活规则", {
    x: 1.12,
    y: 3.95,
    w: 1.8,
    h: 0.18,
    fontSize: 11,
    bold: true,
    color: C.ink,
    margin: 0,
  });
  addBulletList(slide, 1.12, 4.3, 5.0, [
    "expert 已被 bound_experts 绑定",
    "required_doc_types 与本次 review 文档匹配",
    "changed_files 命中 activation_hints",
    "diff / repo context / mode 等上下文满足",
  ]);

  addRoundedCard(slide, 6.75, 3.7, 5.6, 2.42, { fill: C.white, lineColor: C.line, shadow: true });
  slide.addText("为什么这套机制重要", {
    x: 6.98,
    y: 3.95,
    w: 1.9,
    h: 0.18,
    fontSize: 11,
    bold: true,
    color: C.ink,
    margin: 0,
  });
  addBulletList(slide, 6.98, 4.3, 4.95, [
    "新增能力优先只改 extensions，不破坏主审核流程",
    "同一个 tool 结果可被多个专家复用，例如设计文档结构化结果",
    "Skill 决定“何时做什么检查”，而不是把判断交给 LLM 自由发挥",
  ]);
  finalizeSlide(slide);
}

function addMultiAgentSlide() {
  const slide = pptx.addSlide();
  addCanvas(slide);
  addBackdropShapes(slide);
  addTopBand(slide, "05 AGENTS", "多 Agent 智能体架构：不是并排堆模型，而是明确的协作边界", "系统把“谁决策、谁审查、谁取证、谁裁决”拆成稳定角色。", C.plum);

  addNode(slide, 0.86, 1.9, 2.25, 1.2, "主 Agent", "负责理解 diff、评估上下文、挑专家、控制节奏", {
    fill: C.darkPanel,
    lineColor: C.darkPanel,
    titleColor: C.white,
    bodyColor: "D9E4EF",
  });
  addNode(slide, 3.55, 1.9, 2.35, 1.2, "专家 Agent", "每个专家只负责自己的领域规则与风险判断", {
    fill: C.white,
    lineColor: C.line,
    badge: "审查",
    badgeFill: C.skySoft,
  });
  addNode(slide, 6.36, 1.9, 2.35, 1.2, "Tool 层", "负责 repo context、diff、设计文档结构化等证据获取", {
    fill: C.white,
    lineColor: C.line,
    badge: "取证",
    badgeFill: C.tealSoft,
    badgeColor: C.teal,
  });
  addNode(slide, 9.15, 1.9, 2.4, 1.2, "Judge / Human Gate", "负责收敛、降级、裁决与人工确认", {
    fill: C.white,
    lineColor: C.line,
    badge: "收敛",
    badgeFill: C.roseSoft,
    badgeColor: C.rose,
  });
  addArrow(slide, 3.12, 2.4, 0.28, "派工");
  addArrow(slide, 5.94, 2.4, 0.24, "取证");
  addArrow(slide, 8.76, 2.4, 0.22, "收敛");

  addRoundedCard(slide, 0.9, 3.65, 11.55, 2.45, { fill: C.white, lineColor: C.line, shadow: true });
  slide.addText("多 Agent 建设的 4 个关键原则", {
    x: 1.15,
    y: 3.92,
    w: 2.6,
    h: 0.18,
    fontSize: 11,
    bold: true,
    color: C.ink,
    margin: 0,
  });
  const principles = [
    ["边界清晰", "专家之间不能重复审同一类问题，必须各司其职。", C.skySoft],
    ["证据优先", "结论必须能落到 diff、repo context、tool 输出或设计文档证据上。", C.accentSoft],
    ["过程可见", "主 Agent 派工、tool 调用、skill 激活、裁决都必须可追踪。", C.tealSoft],
    ["扩展解耦", "新增能力优先只改 extensions，而不是持续膨胀 ReviewRunner。", C.plumSoft],
  ];
  principles.forEach((item, idx) => {
    addNode(slide, 1.12 + idx * 2.72, 4.32, 2.35, 1.2, item[0], item[1], {
      fill: item[2],
      lineColor: C.line,
      shadow: false,
      titleSize: 10.8,
      bodySize: 8.3,
    });
  });
  finalizeSlide(slide);
}

function addDesignConsistencySlide() {
  const slide = pptx.addSlide();
  addCanvas(slide);
  addBackdropShapes(slide);
  addTopBand(slide, "06 DEEP DIVE", "详细设计一致性检查：首个可插拔能力", "用户在启动页上传设计文档，正确性专家自动激活 design-consistency-check。", C.rose);

  addNode(slide, 0.85, 1.82, 2.25, 0.85, "启动页上传 design spec", "本次 review 私有文档，不混入长期知识库", {
    fill: C.accentSoft,
    lineColor: C.line,
  });
  addNode(slide, 3.42, 1.82, 2.35, 0.85, "Skill 激活", "review 有 design_docs，且变更命中 service / transformer / output", {
    fill: C.goldSoft,
    lineColor: C.line,
  });
  addNode(slide, 6.1, 1.82, 2.42, 0.85, "Tool 展开", "diff_inspector + repo_context_search + design_spec_alignment", {
    fill: C.tealSoft,
    lineColor: C.line,
  });
  addNode(slide, 8.86, 1.82, 3.02, 0.85, "正确性专家结论", "输出 design_alignment_status / missing_design_points / design_conflicts", {
    fill: C.skySoft,
    lineColor: C.line,
  });
  addArrow(slide, 3.12, 2.24, 0.2, "");
  addArrow(slide, 5.9, 2.24, 0.18, "");
  addArrow(slide, 8.68, 2.24, 0.12, "");

  addRoundedCard(slide, 0.85, 3.15, 5.48, 3.05, { fill: C.white, lineColor: C.line, shadow: true });
  slide.addText("design_spec_alignment 提取的结构化设计基线", {
    x: 1.08,
    y: 3.4,
    w: 3.8,
    h: 0.18,
    fontSize: 11,
    bold: true,
    color: C.ink,
    margin: 0,
  });
  const boxes = [
    ["API 定义", C.skySoft],
    ["出入参字段", C.accentSoft],
    ["表结构定义", C.goldSoft],
    ["业务时序", C.tealSoft],
    ["性能要求", C.roseSoft],
    ["安全要求", C.slate],
  ];
  boxes.forEach((box, idx) => {
    const col = idx % 2;
    const row = Math.floor(idx / 2);
    addNode(slide, 1.08 + col * 2.55, 3.84 + row * 0.82, 2.22, 0.58, box[0], "", {
      fill: box[1],
      lineColor: C.line,
      shadow: false,
      titleSize: 10,
    });
  });

  addRoundedCard(slide, 6.58, 3.15, 5.9, 3.05, { fill: C.white, lineColor: C.line, shadow: true });
  slide.addText("最终问题清单会得到什么", {
    x: 6.82,
    y: 3.4,
    w: 2.8,
    h: 0.18,
    fontSize: 11,
    bold: true,
    color: C.ink,
    margin: 0,
  });
  addBulletList(slide, 6.82, 3.82, 5.1, [
    "设计一致性状态：aligned / partially_aligned / misaligned",
    "已满足的设计点、缺失设计点、超出设计的实现",
    "与详细设计冲突的字段、链路或时序",
    "结果页可直接打上“设计不一致”标记并筛选",
  ]);
  addNode(slide, 8.1, 5.32, 2.68, 0.58, "真实 UI 标记", "设计一致性列 + 设计不一致筛选按钮", {
    fill: C.roseSoft,
    lineColor: C.line,
    shadow: false,
    titleSize: 10,
    bodySize: 8.5,
  });
  finalizeSlide(slide);
}

function addWorkbenchSlide() {
  const slide = pptx.addSlide();
  addCanvas(slide, C.canvasAlt);
  addBackdropShapes(slide);
  addTopBand(slide, "07 UI", "前端工作台如何把复杂审核过程讲清楚", "一页里完成启动、过程追踪、结论收敛，但每块职责都很明确。", C.accent);

  addNode(slide, 0.82, 1.88, 3.45, 4.95, "概览与启动", "", {
    fill: C.white,
    lineColor: C.line,
    shadow: true,
  });
  addNode(slide, 0.98, 2.28, 3.1, 0.7, "输入本次审核上下文", "代码链接、模式、专家、详细设计文档", {
    fill: C.skySoft,
    shadow: false,
  });
  addNode(slide, 0.98, 3.18, 3.1, 0.7, "启动约束清晰", "移除 access_token / repo_id / project_id，降低误操作", {
    fill: C.accentSoft,
    shadow: false,
  });
  addNode(slide, 0.98, 4.08, 3.1, 0.7, "系统状态提示", "缺少专家、缺少 repo、待人工裁决都能直接感知", {
    fill: C.tealSoft,
    shadow: false,
  });
  addNode(slide, 0.98, 4.98, 3.1, 0.7, "设计文档上传", "让“本次需求设计”成为 review 级输入", {
    fill: C.goldSoft,
    shadow: false,
  });

  addNode(slide, 4.55, 1.88, 4.15, 4.95, "审核过程", "", {
    fill: C.white,
    lineColor: C.line,
    shadow: true,
  });
  addNode(slide, 4.78, 2.42, 3.68, 0.65, "实时对话流", "主 Agent、专家、tool 调用、skill 激活都显式展示", {
    fill: C.accentSoft,
    shadow: false,
  });
  addNode(slide, 4.78, 3.24, 3.68, 0.95, "GitHub 风格 Diff", "左侧文件树，右侧详细 diff，固定高度滚动", {
    fill: C.skySoft,
    shadow: false,
  });
  addNode(slide, 4.78, 4.38, 3.68, 0.65, "专家路由提示", "用户选择 / 系统补入 / 已跳过，体验更可解释", {
    fill: C.tealSoft,
    shadow: false,
  });
  addNode(slide, 4.78, 5.2, 3.68, 0.75, "设计一致性可见", "过程页直接看到 skill、design docs 和一致性状态", {
    fill: C.roseSoft,
    shadow: false,
  });

  addNode(slide, 8.98, 1.88, 3.52, 4.95, "结论与行动", "", {
    fill: C.white,
    lineColor: C.line,
    shadow: true,
  });
  addNode(slide, 9.2, 2.42, 3.08, 0.65, "问题清单", "设计不一致、已核验、待人工裁决都可筛选", {
    fill: C.roseSoft,
    shadow: false,
  });
  addNode(slide, 9.2, 3.24, 3.08, 0.75, "问题详情弹窗", "修改思路、修复步骤、当前代码、建议代码并排展示", {
    fill: C.goldSoft,
    shadow: false,
  });
  addNode(slide, 9.2, 4.18, 3.08, 0.75, "人工裁决卡", "批准 / 驳回与摘要同屏，不把人审藏起来", {
    fill: C.accentSoft,
    shadow: false,
  });
  addNode(slide, 9.2, 5.12, 3.08, 0.75, "报告摘要", "高风险、设计不一致、已验证、待裁决一眼可见", {
    fill: C.skySoft,
    shadow: false,
  });
  finalizeSlide(slide);
}

function addArtifactSlide() {
  const slide = pptx.addSlide();
  addCanvas(slide);
  addBackdropShapes(slide);
  addTopBand(slide, "08 DATA", "审查过程如何沉淀为可治理产物", "Review 不只是一个状态值，而是一套可回放、可裁决、可追责的产物模型。", C.sky);

  const centers = [1.6, 4.1, 6.6, 9.1, 11.6];
  const titles = ["Review", "Message", "Finding", "Issue", "Report"];
  const subs = [
    "任务主体、平台信息、模式、设计文档",
    "主 Agent / 专家 / tool / 系统提示",
    "单条风险、证据、修复建议、设计一致性",
    "收敛后的问题条目与状态",
    "最终摘要、人工裁决与操作建议",
  ];
  centers.forEach((cx, idx) => {
    addNode(slide, cx - 0.85, 2.55, 1.7, 1.35, titles[idx], subs[idx], {
      fill: idx % 2 === 0 ? C.white : C.canvasAlt,
      lineColor: C.line,
      shadow: true,
      titleSize: 12,
      bodySize: 8.2,
    });
    if (idx < centers.length - 1) {
      addArrow(slide, cx + 0.88, 3.1, 0.75, "沉淀");
    }
  });

  slide.addText("为什么这套产物模型重要", {
    x: 1.12,
    y: 4.72,
    w: 2.6,
    h: 0.18,
    fontSize: 11,
    bold: true,
    color: C.ink,
    margin: 0,
  });
  slide.addShape(pptx.ShapeType.line, {
    x: 1.1,
    y: 4.98,
    w: 11.0,
    h: 0,
    line: { color: C.line, pt: 1.2 },
  });
  addBulletList(slide, 1.12, 5.12, 10.9, [
    "过程可回放：消息、事件、tool 调用、skill 激活都能在工作台与日志中追踪",
    "结果可治理：finding 与 issue 分离，允许系统判断与人工裁决共存",
    "扩展可观测：新 skill / tool 接入后，可以明确看到它在这次 review 里是否真正被调用",
  ]);
  finalizeSlide(slide);
}

function addEvidenceSlide() {
  const slide = pptx.addSlide();
  addCanvas(slide, C.canvasAlt);
  addBackdropShapes(slide);
  addTopBand(slide, "09 EVIDENCE", "真实案例验证：设计不一致问题真的落进了问题清单", "案例：calcom/cal.com PR #28378 + review rev_1b42286a。", C.teal);

  addMetric(slide, 0.88, 1.84, 1.95, "公开仓", "cal.com", C.skySoft);
  addMetric(slide, 3.0, 1.84, 1.65, "PR", "#28378", C.accentSoft);
  addMetric(slide, 4.82, 1.84, 2.2, "本次审核", "rev_1b42286a", C.tealSoft);
  addMetric(slide, 7.2, 1.84, 1.95, "设计文档", "1 份", C.goldSoft);
  addMetric(slide, 9.32, 1.84, 2.1, "结果", "waiting_human", C.roseSoft);

  addRoundedCard(slide, 0.88, 3.35, 5.7, 2.7, { fill: C.white, lineColor: C.line, shadow: true });
  slide.addText("链路证据", {
    x: 1.1,
    y: 3.62,
    w: 1.5,
    h: 0.18,
    fontSize: 11,
    bold: true,
    color: C.ink,
    margin: 0,
  });
  addBulletList(slide, 1.1, 3.98, 5.1, [
    "messages.json 的 expert_ack 中出现 active_skills = [design-consistency-check]",
    "events.json 显示 diff_inspector / repo_context_search / design_spec_alignment 被顺序调用",
    "repo_context_search 命中 getScheduleListItemData.ts、output-schedules.service.ts、schedule.output.ts",
    "design_spec_alignment 输出 design_alignment_status = partially_aligned",
  ], C.inkSoft, 9.2);

  addRoundedCard(slide, 6.82, 3.35, 5.65, 2.7, { fill: C.white, lineColor: C.line, shadow: true });
  slide.addText("结果页表现", {
    x: 7.05,
    y: 3.62,
    w: 1.8,
    h: 0.18,
    fontSize: 11,
    bold: true,
    color: C.ink,
    margin: 0,
  });
  addNode(slide, 7.08, 4.08, 2.0, 0.58, "筛选按钮", "设计不一致 2", {
    fill: C.roseSoft,
    shadow: false,
  });
  addNode(slide, 9.3, 4.08, 2.6, 0.58, "问题列", "设计一致性：部分偏离设计", {
    fill: C.skySoft,
    shadow: false,
  });
  addNode(slide, 7.08, 4.95, 4.82, 0.72, "详情弹窗", "展示 design_doc_titles、missing_design_points、design_conflicts", {
    fill: C.goldSoft,
    shadow: false,
  });
  finalizeSlide(slide);
}

function addBuildMethodSlide() {
  const slide = pptx.addSlide();
  addCanvas(slide);
  addBackdropShapes(slide);
  addTopBand(slide, "09 METHOD", "如何建设一套可治理的多 Agent 代码审核系统", "这不是“多调用几个模型”，而是一套工程化建设方法。", C.mint);

  const methodSteps = [
    ["1. 先定角色", "先切清主 Agent、专家、Judge、Human Gate 的职责，再写 prompt。", C.skySoft],
    ["2. 再定证据", "明确每个专家依赖哪些上下文、哪些 tool、哪些规范文档。", C.accentSoft],
    ["3. 再做治理", "把输出结构化，让每条 finding 能进入汇总、验证和人工裁决。", C.tealSoft],
    ["4. 最后做扩展", "把新能力沉淀成 skill + tool，而不是塞到主流程里。", C.goldSoft],
  ];
  methodSteps.forEach((item, idx) => {
    addNode(slide, 0.95 + idx * 3.03, 2.2, 2.55, 1.7, item[0], item[1], {
      fill: item[2],
      lineColor: C.line,
      shadow: true,
      titleSize: 12,
      bodySize: 8.5,
    });
  });

  addRoundedCard(slide, 0.95, 4.45, 11.45, 1.7, { fill: C.white, lineColor: C.line, shadow: true });
  slide.addText("建设方法的落地顺序", {
    x: 1.18,
    y: 4.72,
    w: 2.2,
    h: 0.18,
    fontSize: 11,
    bold: true,
    color: C.ink,
    margin: 0,
  });
  addBulletList(slide, 1.18, 5.02, 10.7, [
    "先打通一条真实用例：真实 diff、真实 repo、真实专家与真实问题清单",
    "再逐步接入更重的能力：设计一致性、数据库契约、性能与安全专项",
    "最后再做治理与扩展：插件目录、专家中心、结果页标记与历史回放",
  ]);
  finalizeSlide(slide);
}

function addExtensionSlide() {
  const slide = pptx.addSlide();
  addCanvas(slide);
  addBackdropShapes(slide);
  addTopBand(slide, "10 EXTENSION", "如何在不改主源码的前提下扩展能力", "开放出去的是 extensions；专家能力通过 skill + tool 插件接入。", C.gold);

  addNode(slide, 0.92, 2.0, 3.2, 3.7, "开发者新增一个领域能力", "", {
    fill: C.white,
    lineColor: C.line,
    shadow: true,
  });
  addBulletList(slide, 1.14, 2.42, 2.75, [
    "在 extensions/skills 下新增一个 skill 目录",
    "写 SKILL.md 描述能力边界与输出契约",
    "在 metadata.json 配 bound_experts、required_tools、activation_hints",
    "必要时在 extensions/tools 下新增 run.py",
  ], C.inkSoft, 9.5);

  addNode(slide, 4.52, 2.0, 3.05, 3.7, "系统运行时会做什么", "", {
    fill: C.white,
    lineColor: C.line,
    shadow: true,
  });
  addBulletList(slide, 4.74, 2.42, 2.6, [
    "扫描 skill / tool 插件目录",
    "把 extension 绑定关系合并到专家有效配置",
    "按上下文判定 skill 是否应该激活",
    "展开对应 tools 并把结果注入专家 prompt",
  ], C.inkSoft, 9.5);

  addNode(slide, 7.95, 2.0, 4.35, 3.7, "为什么这对平台很重要", "", {
    fill: C.white,
    lineColor: C.line,
    shadow: true,
  });
  addBulletList(slide, 8.18, 2.42, 3.85, [
    "主审核流程不再因为一个新能力持续膨胀",
    "不同团队可以按自己的领域需求添加定制检查",
    "同一条 review 链可以显式给出 skill 激活和 tool 调用证据",
    "能力真正变成“可治理的插件”，而不是隐藏 prompt 魔法",
  ], C.inkSoft, 9.6);

  addNode(slide, 4.7, 6.02, 3.9, 0.52, "推荐接下来的扩展方向", "API 契约检查 / 缓存一致性 / 事务边界 / 幂等性 / 消息顺序", {
    fill: C.darkPanel,
    lineColor: C.darkPanel,
    titleColor: C.white,
    bodyColor: "D9E4EF",
    shadow: false,
    titleSize: 10.5,
    bodySize: 8.4,
  });
  finalizeSlide(slide);
}

function addCloseSlide() {
  const slide = pptx.addSlide();
  addCanvas(slide, C.darkPanel);
  slide.addText("把代码审查从“经验判断”升级为“可编排、可追证、可扩展的系统”", {
    x: 0.9,
    y: 1.15,
    w: 10.8,
    h: 0.68,
    fontSize: 23,
    color: C.white,
    bold: true,
    margin: 0,
  });
  slide.addText("Multi Code Review Agent", {
    x: 0.92,
    y: 2.02,
    w: 3.0,
    h: 0.22,
    fontSize: 12,
    color: "D2DCE7",
    margin: 0,
  });
  addNode(slide, 0.95, 3.05, 3.45, 1.65, "现在已经做到", "真实 PR diff、repo context、规范文档、skill + tool、设计一致性检查、人工裁决闭环", {
    fill: "1A324A",
    lineColor: "244865",
    titleColor: C.white,
    bodyColor: "D9E4EF",
    shadow: false,
  });
  addNode(slide, 4.82, 3.05, 3.45, 1.65, "平台价值", "让专家知识沉淀成系统能力，把“为什么这样判断”明确展示给开发者", {
    fill: "1A324A",
    lineColor: "244865",
    titleColor: C.white,
    bodyColor: "D9E4EF",
    shadow: false,
  });
  addNode(slide, 8.7, 3.05, 3.45, 1.65, "下一阶段", "更多插件化检查、更多平台 provider、更强的可观测性与治理面板", {
    fill: "1A324A",
    lineColor: "244865",
    titleColor: C.white,
    bodyColor: "D9E4EF",
    shadow: false,
  });
  addPill(slide, 4.92, 5.45, 3.45, "核心公式：真实上下文 + 专家边界 + Skill 编排 + Tool 证据", "2B4C6A", C.white);
  finalizeSlide(slide);
}

function writeDiagramSources() {
  const diagramDir = path.join(OUT_DIR, "drawio-mermaid");
  fs.mkdirSync(diagramDir, { recursive: true });
  const diagrams = {
    "01-overall-architecture.mmd": `flowchart LR
  U["用户 / 审核者"] --> FE["React 工作台"]
  FE --> API["FastAPI API"]
  FE --> SSE["SSE 事件流"]
  API --> RS["ReviewService"]
  RS --> PA["PlatformAdapter"]
  RS --> RR["ReviewRunner"]
  RR --> MA["Main Agent"]
  RR --> EXP["Expert Agents"]
  RR --> TG["Tool Gateway"]
  RR --> LLM["LLM Chat Service"]
  TG --> RCS["Repo Context"]
  TG --> DIFF["Diff Inspector"]
  TG --> KS["Knowledge Search"]
  RR --> STORE["Storage / config.json / extensions"]`,
    "02-review-lifecycle.mmd": `flowchart LR
  A["输入 PR + 模式 + 专家 + design spec"] --> B["拉取真实 patch 与 changed_files"]
  B --> C["主 Agent 按 hunk / symbol 派工"]
  C --> D["专家激活 skill"]
  D --> E["展开 tools 并读取 repo context"]
  E --> F["生成 finding"]
  F --> G["judge 合并 issue"]
  G --> H["human gate / 结果页"]`,
    "03-design-consistency.mmd": `flowchart LR
  U["启动页上传 design spec.md"] --> R["review.subject.metadata.design_docs"]
  R --> S["design-consistency-check"]
  S --> T1["diff_inspector"]
  S --> T2["repo_context_search"]
  S --> T3["design_spec_alignment"]
  T3 --> SD["结构化设计基线: API / 字段 / 表 / 时序 / 性能 / 安全"]
  SD --> E["correctness_business"]
  E --> F["design_alignment_status + missing_design_points + design_conflicts"]`,
    "04-skill-tool-extension.mmd": `flowchart LR
  DEV["开发者"] --> SK["extensions/skills/<name>/SKILL.md"]
  DEV --> META["metadata.json"]
  DEV --> TOOL["extensions/tools/<tool>/run.py"]
  META --> ACT["Skill Activation Engine"]
  ACT --> CALL["Tool Execution"]
  CALL --> EXP["Expert Prompt"]
  EXP --> OUT["Finding / Issue / Report"]`,
  };
  Object.entries(diagrams).forEach(([name, content]) => {
    fs.writeFileSync(path.join(diagramDir, name), content, "utf8");
  });
}

async function main() {
  addCoverSlide();
  addWhySlide();
  addArchitectureSlide();
  addHouseArchitectureSlide();
  addLifecycleSlide();
  addCapabilitySlide();
  addMultiAgentSlide();
  addDesignConsistencySlide();
  addWorkbenchSlide();
  addArtifactSlide();
  addEvidenceSlide();
  addBuildMethodSlide();
  addExtensionSlide();
  addCloseSlide();

  writeDiagramSources();
  await pptx.writeFile({ fileName: OUT_FILE });
  console.log(`Wrote ${OUT_FILE}`);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
