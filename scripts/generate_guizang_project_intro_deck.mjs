import { copyFileSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const templatePath = "/Users/neochen/.codex/skills/guizang-ppt-skill/assets/template.html";
const outPath = resolve(root, "output/guizang-project-intro/multi-codereview-agent-research.html");

const customCss = `
  .research-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:1.4vw;margin-top:4vh}
  .research-card{border:1px solid rgba(127,127,127,.28);padding:2.4vh 1.5vw;min-height:18vh;background:rgba(127,127,127,.05)}
  .research-card .num{font-family:var(--serif-en);font-size:3.8vw;font-weight:800;line-height:.9;opacity:.85}
  .research-card .label{font-family:var(--serif-zh);font-size:1.35vw;font-weight:700;margin-top:1.4vh}
  .research-card .desc{font-family:var(--sans-zh);font-size:1vw;line-height:1.55;opacity:.72;margin-top:1vh}
  .diagram{border:1px solid rgba(127,127,127,.28);background:rgba(127,127,127,.045);padding:2vh 1.6vw}
  .diagram-title{font-family:var(--mono);font-size:.78vw;letter-spacing:.2em;text-transform:uppercase;opacity:.6;margin-bottom:1.6vh}
  .node{border:1px solid currentColor;padding:1.35vh 1vw;background:rgba(127,127,127,.045);font-family:var(--sans-zh);font-size:.98vw;line-height:1.45}
  .node strong{font-family:var(--serif-zh);font-size:1.15vw}
  .node.muted{opacity:.72}
  .arrow{font-family:var(--mono);opacity:.52;text-align:center;align-self:center}
  .flow-row{display:grid;grid-template-columns:1fr .24fr 1fr .24fr 1fr .24fr 1fr;gap:1vw;align-items:stretch}
  .stack{display:grid;gap:1.2vh}
  .agent-matrix{display:grid;grid-template-columns:1.1fr 1.1fr 1.1fr;gap:1.4vw;margin-top:4vh}
  .agent-box{border-top:2px solid currentColor;padding-top:1.8vh}
  .agent-box h3{font-family:var(--serif-zh);font-size:1.65vw;margin-bottom:1.2vh}
  .agent-box p{font-family:var(--sans-zh);font-size:1vw;line-height:1.55;opacity:.75}
  .mini-list{display:grid;gap:.8vh;margin-top:1.5vh}
  .mini-list span{font-family:var(--mono);font-size:.78vw;letter-spacing:.12em;text-transform:uppercase;border:1px solid rgba(127,127,127,.28);padding:.7vh .8vw}
  .swimlane{display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:1.2vw;margin-top:4vh}
  .lane{border-left:2px solid currentColor;padding-left:1.1vw;min-height:44vh}
  .lane .t{font-family:var(--serif-zh);font-weight:700;font-size:1.35vw;margin-bottom:1.4vh}
  .lane .d{font-family:var(--sans-zh);font-size:.98vw;line-height:1.55;opacity:.74}
  .lane .step{margin-top:1.2vh;border:1px solid rgba(127,127,127,.28);padding:1vh .8vw;font-size:.88vw}
  .architecture-svg{width:100%;height:49vh;display:block}
  .architecture-svg text{font-family:var(--sans-zh);fill:currentColor}
  .architecture-svg .mono{font-family:var(--mono);letter-spacing:.08em}
  .architecture-svg .box{fill:rgba(127,127,127,.06);stroke:currentColor;stroke-opacity:.42}
  .architecture-svg .accent{fill:rgba(127,127,127,.11);stroke:currentColor;stroke-opacity:.65}
  .architecture-svg .line{stroke:currentColor;stroke-opacity:.5;stroke-width:1.4;fill:none;marker-end:url(#arrowHead)}
  .table-lite{display:grid;grid-template-columns:1fr 1.2fr 1.2fr;gap:0;border-top:1px solid rgba(127,127,127,.32);margin-top:4vh}
  .table-lite > div{border-bottom:1px solid rgba(127,127,127,.28);padding:1.35vh 1vw;font-family:var(--sans-zh);font-size:.96vw;line-height:1.45}
  .table-lite .head{font-family:var(--mono);font-size:.78vw;letter-spacing:.16em;text-transform:uppercase;opacity:.62}
  .takeaway{font-family:var(--serif-zh);font-size:2.1vw;line-height:1.35;font-weight:600}
`;

const slides = String.raw`
<section class="slide hero dark">
  <div class="chrome"><div>Research Deck · Multi-Agent Code Review</div><div>Vol. 2026</div></div>
  <div class="frame" style="display:grid;gap:4vh;align-content:center;min-height:80vh">
    <div class="kicker" data-anim>Internal Research Brief</div>
    <h1 class="h-hero" style="font-size:7.8vw" data-anim>多专家代码审查系统</h1>
    <h2 class="h-sub" data-anim>把复杂 MR 审查，从个人经验变成可复用的工程流程</h2>
    <p class="lead" style="max-width:64vw" data-anim>
      本工具基于 FastAPI、React、LangGraph-style 运行时和多领域专家 Agent，面向真实 MR 提供分工审查、证据补充、影响分析与结果收敛。
    </p>
    <div class="meta-row" data-anim><span>Code Review</span><span>·</span><span>Agent Runtime</span><span>·</span><span>GitNexus Impact</span></div>
  </div>
  <div class="foot"><div>multi-codereview-agent</div><div>科研风格介绍稿</div></div>
</section>

<section class="slide light">
  <div class="chrome"><div>Problem Statement · Review Load</div><div>01 / 10</div></div>
  <div class="frame" style="padding-top:6vh">
    <div class="kicker" data-anim>为什么需要它</div>
    <h2 class="h-xl" style="font-size:4.8vw" data-anim>传统 Review 的瓶颈：不是没人看，而是很难稳定看全。</h2>
    <div class="research-grid">
      <div class="research-card" data-anim>
        <div class="num">01</div><div class="label">上下文散落</div>
        <div class="desc">diff、目标分支源码、设计文档、规范、数据库元信息、历史问题分散在不同地方。</div>
      </div>
      <div class="research-card" data-anim>
        <div class="num">02</div><div class="label">专家经验不可复制</div>
        <div class="desc">资深同学知道该看事务、锁、边界、幂等；新人常常只看到局部代码写法。</div>
      </div>
      <div class="research-card" data-anim>
        <div class="num">03</div><div class="label">结论难以收敛</div>
        <div class="desc">风险、假设、正式问题混在一起，容易出现重复问题或低置信发现被当成 issue。</div>
      </div>
      <div class="research-card" data-anim>
        <div class="num">04</div><div class="label">影响范围靠脑补</div>
        <div class="desc">MR 改了一个方法后，可能波及哪些入口和测试范围，人工很难在短时间内完整推断。</div>
      </div>
    </div>
    <div class="callout" style="margin-top:5vh;max-width:72vw" data-anim>
      这个系统的定位不是替代人工 Review，而是把“应该怎么看”固化成一条可执行、可追踪、可改进的审查流水线。
    </div>
  </div>
  <div class="foot"><div>现状与待改进点</div><div>Context · Evidence · Convergence</div></div>
</section>

<section class="slide dark">
  <div class="chrome"><div>Value Proposition · Advantages</div><div>02 / 10</div></div>
  <div class="frame grid-2-6-6" style="padding-top:6vh;gap:4vw">
    <div class="col" data-anim="left">
      <div class="kicker">工具优势</div>
      <h2 class="h-xl" style="font-size:5.4vw">把一次 Review 拆成四个可控变量</h2>
      <p class="lead">不是让一个大模型“一口气看完所有东西”，而是把专业分工、上下文隔离、证据补充和阈值收敛分开治理。</p>
      <div class="callout"><span class="q-big">核心收益：更容易发现高风险问题，也更容易解释为什么这个问题成立。</span></div>
    </div>
    <div class="diagram" data-anim="right" style="margin-top:3vh">
      <div class="diagram-title">Review Quality Variables</div>
      <div class="stack">
        <div class="node"><strong>专业分工</strong><br>正确性、DDD、数据库、性能、安全、测试等专家各看各的边界。</div>
        <div class="arrow">↓</div>
        <div class="node"><strong>上下文隔离</strong><br>每个专家只拿和职责相关的 diff、规范、源码上下文与工具结果。</div>
        <div class="arrow">↓</div>
        <div class="node"><strong>证据补充</strong><br>工具层提供知识库检索、源码上下文、diff 片段、测试面、GitNexus 图谱。</div>
        <div class="arrow">↓</div>
        <div class="node"><strong>结果收敛</strong><br>按主责专家、置信度、阈值、证据链过滤成“有效问题清单”。</div>
      </div>
    </div>
  </div>
  <div class="foot"><div>Advantage Model</div><div>Specialization · Context · Evidence · Gate</div></div>
</section>

<section class="slide light">
  <div class="chrome"><div>System Architecture · Macro View</div><div>03 / 10</div></div>
  <div class="frame" style="padding-top:5vh">
    <div class="kicker" data-anim>总体架构</div>
    <h2 class="h-xl" style="font-size:4.5vw" data-anim>前端可见 · 后端编排 · 工具补证</h2>
    <div class="diagram" style="margin-top:4vh" data-anim>
      <svg class="architecture-svg" viewBox="0 0 1200 560" role="img" aria-label="系统总体架构图">
        <defs><marker id="arrowHead" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="currentColor" opacity=".55"/></marker></defs>
        <rect class="box" x="30" y="190" width="170" height="120" rx="8"/><text x="115" y="238" text-anchor="middle" font-size="20" font-weight="700">用户</text><text x="115" y="270" text-anchor="middle" font-size="14">MR 链接 / 分支 / 配置</text>
        <rect class="accent" x="250" y="70" width="210" height="360" rx="10"/><text x="355" y="110" text-anchor="middle" font-size="20" font-weight="700">React 工作台</text><text x="355" y="148" text-anchor="middle" font-size="14">概览与启动</text><text x="355" y="190" text-anchor="middle" font-size="14">审核过程</text><text x="355" y="232" text-anchor="middle" font-size="14">结论与行动</text><text x="355" y="274" text-anchor="middle" font-size="14">关联影响报告</text><text x="355" y="340" text-anchor="middle" font-size="13" class="mono">SSE / REST</text>
        <rect class="accent" x="520" y="55" width="250" height="390" rx="10"/><text x="645" y="96" text-anchor="middle" font-size="20" font-weight="700">FastAPI 后端</text><text x="645" y="138" text-anchor="middle" font-size="14">ReviewService</text><text x="645" y="178" text-anchor="middle" font-size="14">PlatformAdapter</text><text x="645" y="218" text-anchor="middle" font-size="14">ReviewRunner</text><text x="645" y="258" text-anchor="middle" font-size="14">MainAgentService</text><text x="645" y="298" text-anchor="middle" font-size="14">Graph / Judge</text><text x="645" y="360" text-anchor="middle" font-size="13" class="mono">state + events</text>
        <rect class="box" x="840" y="40" width="290" height="135" rx="8"/><text x="985" y="78" text-anchor="middle" font-size="18" font-weight="700">证据工具层</text><text x="985" y="112" text-anchor="middle" font-size="14">Knowledge / Repo Context / Diff</text><text x="985" y="142" text-anchor="middle" font-size="14">Test Surface / Tool Plugins</text>
        <rect class="box" x="840" y="220" width="290" height="135" rx="8"/><text x="985" y="258" text-anchor="middle" font-size="18" font-weight="700">外部与本地资源</text><text x="985" y="292" text-anchor="middle" font-size="14">GitHub / GitLab / CodeHub</text><text x="985" y="322" text-anchor="middle" font-size="14">本地多代码仓 / 配置 / 存储</text>
        <rect class="box" x="840" y="400" width="290" height="115" rx="8"/><text x="985" y="438" text-anchor="middle" font-size="18" font-weight="700">模型与图谱</text><text x="985" y="472" text-anchor="middle" font-size="14">LLMChatService</text><text x="985" y="500" text-anchor="middle" font-size="14">GitNexus MCP / Analyze</text>
        <path class="line" d="M200 250 H245"/><path class="line" d="M460 250 H515"/><path class="line" d="M770 150 H835"/><path class="line" d="M770 275 H835"/><path class="line" d="M770 420 H835"/>
      </svg>
    </div>
  </div>
  <div class="foot"><div>Macro Architecture</div><div>Frontend · Runtime · Evidence</div></div>
</section>

<section class="slide hero light">
  <div class="chrome"><div>Execution Graph · Review Runtime</div><div>04 / 10</div></div>
  <div class="frame" style="display:grid;gap:5vh;align-content:center;min-height:80vh">
    <div class="kicker" data-anim>一次审核如何跑起来</div>
    <h1 class="h-hero" style="font-size:6.6vw" data-anim>从 MR 到有效问题清单</h1>
    <div class="flow-row" data-anim>
      <div class="node"><strong>输入归一化</strong><br>MR / Branch / Commit 统一为 ReviewSubject</div>
      <div class="arrow">→</div>
      <div class="node"><strong>主 Agent 路由</strong><br>根据 diff、专家画像、风险信号选择参与专家</div>
      <div class="arrow">→</div>
      <div class="node"><strong>专家深审</strong><br>每个专家拿私有上下文、规范、工具证据</div>
      <div class="arrow">→</div>
      <div class="node"><strong>Graph 收敛</strong><br>去重、置信度、阈值、证据核验、人工 gate</div>
    </div>
    <p class="lead" style="max-width:70vw" data-anim>前端通过 SSE 看到过程事件：专家选择、工具调用、对话流、finding 生成、issue 收敛和最终报告。</p>
  </div>
  <div class="foot"><div>Runtime Path</div><div>Normalize → Route → Review → Judge</div></div>
</section>

<section class="slide dark">
  <div class="chrome"><div>Context Management · Multi-Agent</div><div>05 / 10</div></div>
  <div class="frame grid-2-7-5" style="padding-top:6vh;gap:4vw">
    <div class="col" data-anim="left">
      <div class="kicker">多 Agent 的真正价值</div>
      <h2 class="h-xl" style="font-size:5.4vw">不是“人多热闹”，而是上下文窗口被正确分配。</h2>
      <p class="lead">单 Agent 容易把业务、数据库、性能、安全、测试全部塞进同一个窗口。多 Agent 则把上下文拆成专家私有工作台：每个人只看自己该看的证据。</p>
      <div class="callout">窗口不是越大越好。真正关键的是：哪些证据进入哪个专家的注意力范围。</div>
    </div>
    <div class="diagram" data-anim="right" style="margin-top:2vh">
      <div class="diagram-title">Context Isolation Model</div>
      <div class="stack">
        <div class="node"><strong>共享输入</strong><br>MR diff / 目标分支 / ReviewSubject / Repo Policy</div>
        <div class="arrow">↓ 分片 + 路由 + 证据选择</div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:1vw">
          <div class="node"><strong>正确性专家</strong><br>状态流转、边界条件、注释承诺</div>
          <div class="node"><strong>数据库专家</strong><br>SQL、事务、锁、索引、Schema</div>
          <div class="node"><strong>DDD 专家</strong><br>聚合边界、分层职责、依赖方向</div>
          <div class="node"><strong>性能专家</strong><br>循环放大、并发、资源与失败恢复</div>
        </div>
        <div class="arrow">↓ 结构化 findings</div>
        <div class="node"><strong>统一收敛层</strong><br>主责归因、重复合并、置信度阈值、证据核验</div>
      </div>
    </div>
  </div>
  <div class="foot"><div>Context Window as Design Resource</div><div>Shared Input · Private Context · Shared Judgment</div></div>
</section>

<section class="slide light">
  <div class="chrome"><div>Expert System · Responsibility Boundary</div><div>06 / 10</div></div>
  <div class="frame" style="padding-top:5vh">
    <div class="kicker" data-anim>专家体系</div>
    <h2 class="h-xl" style="font-size:4.7vw" data-anim>专家不是堆角色，而是划清责任边界。</h2>
    <div class="agent-matrix">
      <div class="agent-box" data-anim>
        <h3>通用层专家</h3>
        <p>覆盖跨项目高频问题：编码规范、DDD 边界、业务正确性、可维护性、测试、安全。</p>
        <div class="mini-list"><span>coding standard</span><span>ddd architecture</span><span>business correctness</span><span>test verification</span></div>
      </div>
      <div class="agent-box" data-anim>
        <h3>专项层专家</h3>
        <p>覆盖基础设施和中间件：数据库、Redis、MQ、性能可靠性、前端可访问性。</p>
        <div class="mini-list"><span>database</span><span>redis</span><span>mq</span><span>performance</span></div>
      </div>
      <div class="agent-box" data-anim>
        <h3>分析型专家</h3>
        <p>关联性影响分析专家默认参与每次 MR，只输出影响范围和建议测试范围，不参与普通 issue 报错。</p>
        <div class="mini-list"><span>gitnexus graph</span><span>impact report</span><span>test scope</span></div>
      </div>
    </div>
    <div class="table-lite" data-anim>
      <div class="head">Rule</div><div class="head">Meaning</div><div class="head">Result</div>
      <div>一个问题类别</div><div>只允许一个主责专家主提</div><div>减少“换个说法重复报”</div>
      <div>行为优先</div><div>行为错误 > 边界错误 > 写法问题 > 维护成本</div><div>先保真，再谈风格</div>
      <div>跨专家协同</div><div>其他专家可提供 expert_views</div><div>最终 issue 仍归唯一主责</div>
    </div>
  </div>
  <div class="foot"><div>Expert Boundary</div><div>One Category · One Primary Expert</div></div>
</section>

<section class="slide dark">
  <div class="chrome"><div>GitNexus · Impact Analysis</div><div>07 / 10</div></div>
  <div class="frame" style="padding-top:5vh">
    <div class="kicker" data-anim>关联性影响分析</div>
    <h2 class="h-xl" style="font-size:4.8vw" data-anim>普通 Review 看缺陷，GitNexus 报告看波及范围。</h2>
    <div class="diagram" style="margin-top:4vh" data-anim>
      <svg class="architecture-svg" viewBox="0 0 1200 520" role="img" aria-label="GitNexus 影响分析流程图">
        <defs><marker id="arrowHead2" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="currentColor" opacity=".55"/></marker></defs>
        <rect class="box" x="50" y="70" width="210" height="120" rx="8"/><text x="155" y="118" text-anchor="middle" font-size="19" font-weight="700">后台建图</text><text x="155" y="152" text-anchor="middle" font-size="14">gitnexus analyze</text>
        <rect class="box" x="50" y="300" width="210" height="120" rx="8"/><text x="155" y="348" text-anchor="middle" font-size="19" font-weight="700">每次 MR</text><text x="155" y="382" text-anchor="middle" font-size="14">diff 提取类 / 方法 / 文件</text>
        <rect class="accent" x="350" y="190" width="230" height="150" rx="10"/><text x="465" y="238" text-anchor="middle" font-size="20" font-weight="700">GitNexus MCP</text><text x="465" y="275" text-anchor="middle" font-size="14">list_repos</text><text x="465" y="302" text-anchor="middle" font-size="14">detect_changes / context / impact</text>
        <rect class="box" x="670" y="88" width="210" height="110" rx="8"/><text x="775" y="132" text-anchor="middle" font-size="18" font-weight="700">调用链路</text><text x="775" y="164" text-anchor="middle" font-size="14">入口 / 上游 / 下游</text>
        <rect class="box" x="670" y="232" width="210" height="110" rx="8"/><text x="775" y="276" text-anchor="middle" font-size="18" font-weight="700">影响对象</text><text x="775" y="308" text-anchor="middle" font-size="14">文件 / 模块 / 服务</text>
        <rect class="box" x="670" y="376" width="210" height="110" rx="8"/><text x="775" y="420" text-anchor="middle" font-size="18" font-weight="700">测试范围</text><text x="775" y="452" text-anchor="middle" font-size="14">must-run / suggested</text>
        <rect class="accent" x="970" y="190" width="190" height="150" rx="10"/><text x="1065" y="242" text-anchor="middle" font-size="20" font-weight="700">影响报告</text><text x="1065" y="278" text-anchor="middle" font-size="14">Markdown 模板</text><text x="1065" y="306" text-anchor="middle" font-size="14">结果页独立标签</text>
        <path d="M260 130 C310 130 305 230 345 244" class="line" marker-end="url(#arrowHead2)"/><path d="M260 360 C315 360 305 305 345 290" class="line" marker-end="url(#arrowHead2)"/>
        <path d="M580 265 H665" class="line" marker-end="url(#arrowHead2)"/><path d="M880 143 C930 143 930 235 965 248" class="line" marker-end="url(#arrowHead2)"/><path d="M880 287 H965" class="line" marker-end="url(#arrowHead2)"/><path d="M880 431 C930 431 930 315 965 302" class="line" marker-end="url(#arrowHead2)"/>
      </svg>
    </div>
  </div>
  <div class="foot"><div>Impact Analysis</div><div>Graph First · MR Query · Report Template</div></div>
</section>

<section class="slide light">
  <div class="chrome"><div>How To Use · Operator Flow</div><div>08 / 10</div></div>
  <div class="frame" style="padding-top:5vh">
    <div class="kicker" data-anim>使用方式</div>
    <h2 class="h-xl" style="font-size:4.8vw" data-anim>研发同学沿着工作台走，复杂编排交给系统。</h2>
    <div class="swimlane">
      <div class="lane" data-anim>
        <div class="t">1. 首页</div>
        <div class="d">查看待审核队列，按 MR 或项目入口进入审核工作台。</div>
        <div class="step">输入 MR 链接</div><div class="step">标题自动带入</div>
      </div>
      <div class="lane" data-anim>
        <div class="t">2. 创建任务</div>
        <div class="d">可手动选择候选专家；如果不选，主 Agent 会自动判断需要哪些专家参与。</div>
        <div class="step">关联影响专家默认参与</div><div class="step">启动审核</div>
      </div>
      <div class="lane" data-anim>
        <div class="t">3. 查看过程</div>
        <div class="d">审核过程页展示专家对话流、专家泳道、diff、工具调用和回放控制台。</div>
        <div class="step">看专家如何判断</div><div class="step">看证据从哪里来</div>
      </div>
      <div class="lane" data-anim>
        <div class="t">4. 处理结果</div>
        <div class="d">结果页展示有效问题、过滤问题、关联影响报告，可查看详情并提交到 CodeHub。</div>
        <div class="step">问题详情</div><div class="step">提交整改</div>
      </div>
    </div>
    <div class="callout" style="margin-top:4vh" data-anim>设置页负责模型、代码仓、GitNexus、Skill、Tool 和报告模板；专家中心负责查看和维护专家配置。</div>
  </div>
  <div class="foot"><div>Operator Experience</div><div>Queue · Workbench · Result · Settings</div></div>
</section>

<section class="slide hero dark">
  <div class="chrome"><div>Research Takeaway · Next Step</div><div>10 / 10</div></div>
  <div class="frame" style="display:grid;gap:5vh;align-content:center;min-height:80vh">
    <div class="kicker" data-anim>结论</div>
    <h1 class="h-hero" style="font-size:6.7vw" data-anim>这不是一个“AI 给建议”的工具。</h1>
    <p class="takeaway" style="max-width:76vw" data-anim>
      它更像一套代码审查实验平台：把专家职责、上下文选择、工具证据、影响分析和结果阈值都工程化，让团队可以持续调优 Review 质量。
    </p>
    <div class="flow-row" data-anim>
      <div class="node"><strong>现在可用</strong><br>MR 审核、多专家、结果收敛、影响报告</div>
      <div class="arrow">→</div>
      <div class="node"><strong>持续演进</strong><br>专家边界、质量评测、误报控制、上下文检索</div>
      <div class="arrow">→</div>
      <div class="node"><strong>团队价值</strong><br>把资深 Review 方法沉淀为普通研发也能使用的工作流</div>
      <div class="arrow">→</div>
      <div class="node"><strong>接入方式</strong><br>按项目配置代码仓、模型、GitNexus 与 Repo Policy</div>
    </div>
  </div>
  <div class="foot"><div>Takeaway</div><div>Review Quality as an Engineering System</div></div>
</section>
`;

mkdirSync(dirname(outPath), { recursive: true });
mkdirSync(resolve(dirname(outPath), "assets"), { recursive: true });

let html = readFileSync(templatePath, "utf8");
html = html.replace("[必填] 替换为 PPT 标题 · Deck Title", "多专家代码审查系统 · Research Deck");
html = html.replace(
  /--ink:#0a0a0b;\n    --ink-rgb:10,10,11;\n    --paper:#f1efea;\n    --paper-rgb:241,239,234;\n    --paper-tint:#e8e5de;\n    --ink-tint:#18181a;/,
  "--ink:#0a1f3d;\n    --ink-rgb:10,31,61;\n    --paper:#f1f3f5;\n    --paper-rgb:241,243,245;\n    --paper-tint:#e4e8ec;\n    --ink-tint:#152a4a;",
);
html = html.replace("</style>", `${customCss}\n</style>`);
html = html.replace("<!-- SLIDES_HERE -->", slides);

writeFileSync(outPath, html, "utf8");
copyFileSync("/Users/neochen/.codex/skills/guizang-ppt-skill/assets/motion.min.js", resolve(dirname(outPath), "assets/motion.min.js"));
console.log(outPath);
