const fs = require("fs");
const path = require("path");
const PptxGenJS = require("../project-intro-slides/node_modules/pptxgenjs");
const {
  warnIfSlideHasOverlaps,
  warnIfSlideElementsOutOfBounds,
} = require("../project-intro-slides/pptxgenjs_helpers/layout");

const pptx = new PptxGenJS();
pptx.layout = "LAYOUT_WIDE";
pptx.author = "Codex";
pptx.company = "OpenAI";
pptx.subject = "技术线分享：多专家代码审查工具";
pptx.title = "多专家代码审查工具介绍";
pptx.lang = "zh-CN";
pptx.theme = {
  headFontFace: "Aptos",
  bodyFontFace: "Aptos",
  lang: "zh-CN",
};

const OUT_DIR = __dirname;
const FINAL_DIR = path.join(__dirname, "../../docs/presentations");
const OUT_FILE = path.join(OUT_DIR, "tech-review-tool-share-4page-v2.pptx");
const FINAL_FILE = path.join(FINAL_DIR, "tech-review-tool-share-4page-v2.pptx");

const C = {
  bg: "F6F8FB",
  bgAlt: "FFFFFF",
  ink: "17324D",
  soft: "5D7288",
  line: "D7E1EA",
  blue: "356AE6",
  blueSoft: "ECF3FF",
  teal: "0E7C72",
  tealSoft: "E9F8F5",
  orange: "F28C28",
  orangeSoft: "FFF3E7",
  red: "D9485F",
  redSoft: "FFF1F3",
  gold: "B7791F",
  goldSoft: "FFF7E8",
  navy: "10253A",
  white: "FFFFFF",
  green: "2F855A",
  greenSoft: "EDF9F1",
};

function addCanvas(slide, bg = C.bg) {
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
    y: 0.42,
    w: 1.36,
    h: 0.26,
    rectRadius: 0.06,
    line: { color: accent, transparency: 100 },
    fill: { color: accent },
  });
  slide.addText(label, {
    x: 0.66,
    y: 0.49,
    w: 1.2,
    h: 0.11,
    fontSize: 8,
    color: C.white,
    bold: true,
    align: "center",
    margin: 0,
  });
  slide.addText(title, {
    x: 0.58,
    y: 0.9,
    w: 10.4,
    h: 0.38,
    fontSize: 23,
    bold: true,
    color: C.ink,
    margin: 0,
  });
  slide.addText(subtitle, {
    x: 0.58,
    y: 1.33,
    w: 10.9,
    h: 0.22,
    fontSize: 10,
    color: C.soft,
    margin: 0,
  });
  slide.addText(page, {
    x: 11.84,
    y: 0.48,
    w: 0.84,
    h: 0.14,
    fontSize: 9,
    color: C.soft,
    bold: true,
    align: "right",
    margin: 0,
  });
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
    shadow: {
      type: "outer",
      color: "DDE6F0",
      blur: 1,
      angle: 45,
      distance: 0.5,
      opacity: 0.08,
    },
  });
}

function addSectionTitle(slide, x, y, text, color = C.ink) {
  slide.addText(text, {
    x,
    y,
    w: 3.8,
    h: 0.16,
    fontSize: 13,
    bold: true,
    color,
    margin: 0,
  });
}

function addBulletList(slide, x, y, w, items, bulletColor = C.blue, textColor = C.ink, size = 10) {
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
      h: 0.3,
      fontSize: size,
      color: textColor,
      margin: 0,
      breakLine: false,
    });
    top += 0.36;
  });
}

function addArrow(slide, x1, y1, x2, y2, color = C.blue) {
  slide.addShape(pptx.ShapeType.line, {
    x: x1,
    y: y1,
    w: x2 - x1,
    h: y2 - y1,
    line: { color, pt: 1.8, beginArrowType: "none", endArrowType: "triangle" },
  });
}

function addPill(slide, x, y, w, text, fill, color = C.ink) {
  slide.addShape(pptx.ShapeType.roundRect, {
    x,
    y,
    w,
    h: 0.26,
    rectRadius: 0.06,
    line: { color: fill, transparency: 100 },
    fill: { color: fill },
  });
  slide.addText(text, {
    x: x + 0.06,
    y: y + 0.06,
    w: w - 0.12,
    h: 0.1,
    fontSize: 8.5,
    color,
    bold: true,
    align: "center",
    margin: 0,
  });
}

function finalize(slide) {
  warnIfSlideHasOverlaps(slide, pptx);
  warnIfSlideElementsOutOfBounds(slide, pptx);
}

function slideCurrentState() {
  const slide = pptx.addSlide();
  addCanvas(slide);
  addHeader(
    slide,
    "Current State",
    "当前 Code Review 的现状与待改进点",
    "这个工具要解决的，不是简单的代码风格检查，而是复杂 MR 的深层风险识别。",
    "01 / 04",
    C.orange
  );

  addCard(slide, 0.62, 1.8, 5.98, 4.95, C.white);
  addSectionTitle(slide, 0.92, 2.06, "我们现在的 review 常见情况");
  slide.addText("简单问题通常能看出来，复杂问题更容易漏。", {
    x: 0.92,
    y: 2.34,
    w: 4.8,
    h: 0.18,
    fontSize: 10.2,
    color: C.soft,
    margin: 0,
  });
  addBulletList(slide, 0.94, 2.8, 5.2, [
    "review 仍然以人工经验为主，不同 reviewer 深度差异很大",
    "对代码规范、空指针、命名问题比较敏感",
    "对业务规则、事务顺序、批量边界、锁风险不一定稳定看出来",
    "复杂 MR 涉及多个领域时，一个人很难同时兼顾业务、架构、数据库和性能",
  ], C.orange);

  addCard(slide, 1.04, 4.65, 5.1, 1.52, C.orangeSoft, C.orangeSoft);
  slide.addText("一个典型复杂 MR 里，经常同时出现：", {
    x: 1.24,
    y: 4.86,
    w: 2.6,
    h: 0.16,
    fontSize: 10.5,
    bold: true,
    color: C.ink,
    margin: 0,
  });
  ["业务逻辑改动", "架构边界调整", "SQL / 事务变更", "批处理 / 并发 / 锁"].forEach((text, idx) => {
    addCard(slide, 1.24 + idx * 1.2, 5.28, 1.02, 0.5, C.white, C.line);
    slide.addText(text, {
      x: 1.28 + idx * 1.2,
      y: 5.43,
      w: 0.94,
      h: 0.14,
      fontSize: 8.6,
      align: "center",
      color: C.ink,
      margin: 0,
    });
  });

  addCard(slide, 6.86, 1.8, 5.86, 4.95, C.white);
  addSectionTitle(slide, 7.16, 2.06, "待改进的 3 个关键点");
  const improvements = [
    ["上下文太杂", "diff、源码、规则、设计文档、数据库信息分散在不同地方", C.blueSoft, C.blue],
    ["视角太单一", "复杂 MR 需要多种专业视角，不适合一个 reviewer 或一个模型硬看全部", C.tealSoft, C.teal],
    ["结果不稳定", "经验难复用，输出不一定带证据，也不一定能直接落到研发动作上", C.redSoft, C.red],
  ];
  let y = 2.52;
  improvements.forEach(([title, body, fill, accent]) => {
    addCard(slide, 7.16, y, 5.2, 0.92, fill, fill);
    addPill(slide, 7.34, y + 0.14, 1.08, title, accent, C.white);
    slide.addText(body, {
      x: 8.58,
      y: y + 0.18,
      w: 3.5,
      h: 0.28,
      fontSize: 9.4,
      color: C.ink,
      margin: 0,
    });
    y += 1.08;
  });
  addCard(slide, 7.16, 5.86, 5.2, 0.68, C.greenSoft, C.greenSoft);
  slide.addText("这也是为什么我们做的不是“单次 AI 看代码”，而是一条多专家审查链路。", {
    x: 7.42,
    y: 6.08,
    w: 4.7,
    h: 0.16,
    fontSize: 10.3,
    bold: true,
    color: C.green,
    margin: 0,
  });
  finalize(slide);
}

function slideArchitecture() {
  const slide = pptx.addSlide();
  addCanvas(slide, C.bgAlt);
  addHeader(
    slide,
    "Architecture",
    "本工具的宏观分层架构图",
    "这一页只看整体，不讲细节实现。",
    "02 / 04",
    C.blue
  );

  slide.addText("从上到下看，这个系统本质上是“入口层 → 执行层 → 分析层 → 扩展层 → 底座层”五层结构。", {
    x: 0.76,
    y: 1.78,
    w: 10.8,
    h: 0.18,
    fontSize: 10.4,
    color: C.soft,
    margin: 0,
  });

  const layerDefs = [
    ["用户入口层", "发起审核任务、查看过程、查看结果", "首页 / 审核工作台 / 历史记录 / 设置页", 0.9, 2.12, 11.54, 0.72, "1", C.blueSoft, C.blue],
    ["应用服务层", "把审核任务组织起来并真正跑起来", "ReviewService / ReviewRunner / AutoReviewScheduler", 1.16, 2.98, 11.0, 0.78, "2", C.tealSoft, C.teal],
    ["智能分析层", "主 Agent 选专家，多专家分别审查，LangGraph 收敛结果", "Main Agent / Experts / LangGraph StateGraph", 1.42, 3.94, 10.46, 0.92, "3", C.orangeSoft, C.orange],
    ["扩展能力层", "按需补规则、补工具、补外部证据", "规范文档 / skill / tool / repo context / pg schema", 1.68, 5.06, 9.92, 0.82, "4", C.goldSoft, C.gold],
    ["平台支撑层", "提供代码仓、配置、日志、消息和报告产物", "本地仓库 / 配置 / 历史消息 / findings / issues / report", 1.94, 6.06, 9.38, 0.72, "5", C.redSoft, C.red],
  ];
  layerDefs.forEach(([title, body, foot, x, y, w, h, num, fill, accent], idx) => {
    addCard(slide, x, y, w, h, fill, fill);
    addPill(slide, x + 0.18, y + 0.2, 0.38, num, accent, C.white);
    slide.addText(title, {
      x: x + 0.72,
      y: y + 0.16,
      w: 2.1,
      h: 0.16,
      fontSize: 12.5,
      bold: true,
      color: C.ink,
      margin: 0,
    });
    slide.addText(body, {
      x: x + 2.82,
      y: y + 0.16,
      w: w - 3.08,
      h: 0.18,
      fontSize: 9.6,
      color: C.ink,
      margin: 0,
    });
    slide.addText(foot, {
      x: x + 0.72,
      y: y + 0.48,
      w: w - 1.0,
      h: 0.14,
      fontSize: 8.6,
      color: C.soft,
      margin: 0,
    });
    if (idx < layerDefs.length - 1) {
      addArrow(slide, 6.66, y + h, 6.66, y + h + 0.14, accent);
    }
  });

  addCard(slide, 0.9, 6.96, 11.54, 0.22, C.white, C.white);
  slide.addText("这页想传达的重点：它不是一个“调用一次模型”的工具，而是一套分层组织起来的代码审查系统。", {
    x: 1.08,
    y: 7.0,
    w: 11.0,
    h: 0.12,
    fontSize: 8.9,
    color: C.ink,
    margin: 0,
    align: "center",
  });
  finalize(slide);
}

function slideVideo() {
  const slide = pptx.addSlide();
  addCanvas(slide);
  addHeader(
    slide,
    "Video",
    "介绍视频怎么拍：建议用一个真实 MR 来讲",
    "不要做功能巡礼，最好用一条真实链路，把“为什么有价值”讲清楚。",
    "03 / 04",
    C.teal
  );

  addSectionTitle(slide, 0.72, 1.86, "视频脚本建议（控制在 3 ~ 5 分钟）");
  const steps = [
    ["1. 问题背景", "先选一个真实 Java MR，说明它为什么复杂，人工 review 为什么容易漏。", C.blueSoft, C.blue],
    ["2. 发起任务", "展示如何在工作台里输入 MR 链接、选择模式、发起审查。", C.tealSoft, C.teal],
    ["3. 看过程页", "重点展示主 Agent 选专家、规则筛选批次、tool 调用、专家开始审查。", C.orangeSoft, C.orange],
    ["4. 看结果页", "展示审核发现、有效问题清单、被阈值过滤的问题，以及专家证据。", C.goldSoft, C.gold],
    ["5. 做总结", "收尾时强调：这套工具最适合复杂 MR，不是为了替代人工，而是让 review 更有深度。", C.redSoft, C.red],
  ];
  let y = 2.18;
  steps.forEach(([title, body, fill, accent], idx) => {
    addCard(slide, 0.94, y, 11.42, 0.68, fill, fill);
    addPill(slide, 1.12, y + 0.16, 1.08, title, accent, C.white);
    slide.addText(body, {
      x: 2.42,
      y: y + 0.19,
      w: 9.3,
      h: 0.22,
      fontSize: 9.5,
      color: C.ink,
      margin: 0,
    });
    if (idx < steps.length - 1) {
      addArrow(slide, 1.52, y + 0.7, 1.52, y + 0.92, accent);
    }
    y += 0.86;
  });

  // Intentional nested layout: the larger green summary card contains four small white tags.
  addCard(slide, 7.92, 6.42, 4.0, 0.74, C.greenSoft, C.greenSoft);
  slide.addText("视频里最值得展示的页面", {
    x: 8.18,
    y: 6.6,
    w: 2.3,
    h: 0.15,
    fontSize: 10,
    bold: true,
    color: C.green,
    margin: 0,
  });
  ["专家参与判定", "规则筛选批次", "tool 结果", "有效问题清单"].forEach((text, idx) => {
    const row = Math.floor(idx / 2);
    const col = idx % 2;
    addCard(slide, 8.16 + col * 1.8, 6.82 + row * 0.24, 1.52, 0.18, C.white, C.white);
    slide.addText(text, {
      x: 8.22 + col * 1.8,
      y: 6.86 + row * 0.24,
      w: 1.4,
      h: 0.08,
      fontSize: 7.8,
      color: C.ink,
      align: "center",
      margin: 0,
    });
  });
  finalize(slide);
}

function slidePlan() {
  const slide = pptx.addSlide();
  addCanvas(slide, C.bgAlt);
  addHeader(
    slide,
    "Next",
    "当前不足与演进计划",
    "方向已经比较清楚了，但这套工具还处在“从可用走向可依赖”的过程中。",
    "04 / 04",
    C.red
  );

  addCard(slide, 0.62, 1.82, 5.92, 3.98, C.white);
  addSectionTitle(slide, 0.92, 2.06, "当前不足");
  addBulletList(slide, 0.94, 2.5, 5.1, [
    "检视质量还不够稳定：有些高价值问题能抓到，但不是每次都稳定",
    "上下文组织还在优化：复杂跨文件场景下，证据有时还不够完整",
    "稳定性仍需提升：大任务、内网 LLM、Windows 环境仍有波动",
    "平台化程度还不够：距离公共机器、多项目共享、统一权限还有距离",
  ], C.red, C.ink, 9.2);

  addCard(slide, 6.82, 1.82, 5.9, 3.98, C.white);
  addSectionTitle(slide, 7.1, 2.06, "下一步怎么推进");
  const roadmap = [
    ["先提质量", "补高价值场景规则和测试，重点把正确性、架构、数据库、性能做深", C.blueSoft, C.blue],
    ["再提稳定性", "继续收超时、重试、恢复和大任务执行链路", C.tealSoft, C.teal],
    ["最后做平台化", "逐步补项目级配置隔离、多租户、统一账号权限", C.goldSoft, C.gold],
  ];
  let y = 2.46;
  roadmap.forEach(([title, body, fill, accent], idx) => {
    addCard(slide, 7.08, y, 5.22, 0.8, fill, fill);
    addPill(slide, 7.28, y + 0.16, 0.96, title, accent, C.white);
    slide.addText(body, {
      x: 8.5,
      y: y + 0.18,
      w: 3.42,
      h: 0.28,
      fontSize: 8.9,
      color: C.ink,
      margin: 0,
    });
    if (idx < roadmap.length - 1) {
      addArrow(slide, 7.74, y + 0.84, 7.74, y + 0.98, accent);
    }
    y += 0.96;
  });

  addCard(slide, 0.94, 6.02, 11.36, 0.46, C.blueSoft, C.blueSoft);
  slide.addText(
    "结论：这套工具已经证明“多专家 + 分层上下文 + 工具取证”这条路是成立的，下一阶段重点是把质量和稳定性做扎实。",
    {
      x: 1.24,
      y: 6.16,
      w: 10.72,
      h: 0.16,
      fontSize: 9.8,
      bold: true,
      color: C.ink,
      margin: 0,
    }
  );
  finalize(slide);
}

slideCurrentState();
slideArchitecture();
slideVideo();
slidePlan();

fs.mkdirSync(OUT_DIR, { recursive: true });
fs.mkdirSync(FINAL_DIR, { recursive: true });

pptx.writeFile({ fileName: OUT_FILE }).then(() => {
  fs.copyFileSync(OUT_FILE, FINAL_FILE);
  console.log(`wrote ${OUT_FILE}`);
  console.log(`copied to ${FINAL_FILE}`);
}).catch((err) => {
  console.error(err);
  process.exit(1);
});
