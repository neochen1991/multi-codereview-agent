from __future__ import annotations

from pathlib import Path
from textwrap import dedent


ROOT = Path("/Users/neochen/multi-codereview-agent")
EXPORT_DIR = ROOT / "docs" / "expert-specs-export"


def lines(text: str) -> int:
    return len(text.rstrip().splitlines())


def section(title: str) -> list[str]:
    return [f"## {title}", ""]


def subsection(title: str) -> list[str]:
    return [f"### {title}", ""]


def render_profile(profile: dict[str, object]) -> str:
    out: list[str] = []
    out.append(f"# {profile['title']}")
    out.append("")
    out.extend(section("文档定位"))
    out.extend(
        [
            f"- 专家 ID: `{profile['id']}`",
            f"- 专家角色: {profile['role']}",
            f"- 核心审视目标: {profile['mission']}",
            "- 使用方式: 审视前必须同时加载 MR/PR diff、目标分支源码上下文、专家绑定 Markdown 文档与本规范。",
            "- 输出原则: 只输出本专家职责边界内的结论；发现跨边界风险时必须显式建议转交其他专家。",
            "",
        ]
    )

    out.extend(section("权威参考来源"))
    for item in profile["sources"]:
        out.append(f"- {item}")
    out.append("")

    out.extend(section("职责边界"))
    out.append("### 本专家必须负责")
    out.append("")
    for item in profile["in_scope"]:
        out.append(f"- {item}")
    out.append("")
    out.append("### 本专家不负责")
    out.append("")
    for item in profile["out_of_scope"]:
        out.append(f"- {item}")
    out.append("")
    out.append("### 遇到这些信号时必须移交")
    out.append("")
    for item in profile["handoff"]:
        out.append(f"- {item}")
    out.append("")

    out.extend(section("固定审视流程"))
    out.extend(
        [
            "1. 先读 MR/PR 的目标 hunk，定位本次改动真正改了什么。",
            "2. 再去目标分支源码仓检索同名方法、同名类型、调用方和被调用方。",
            "3. 按本规范的维度逐条核对，只保留本专家职责边界内的直接证据。",
            "4. 对证据不足的问题降级为待验证风险，不能因为经验感觉直接定性。",
            "5. 修复建议必须说清楚怎么改、在哪改、哪些测试或验证要补齐。",
            "",
        ]
    )

    out.extend(section("源码仓检索策略"))
    out.extend(
        [
            "### 首选检索入口",
            "",
        ]
    )
    for item in profile["repo_queries"]:
        out.append(f"- {item}")
    out.append("")
    out.extend(
        [
            "### 证据闭环要求",
            "",
        ]
    )
    for item in profile["evidence"]:
        out.append(f"- {item}")
    out.append("")

    out.extend(section("详细审视维度"))
    for idx, dimension in enumerate(profile["dimensions"], start=1):
        out.append(f"### 维度 {idx}: {dimension['name']}")
        out.append("")
        out.append(f"- 维度目标: {dimension['objective']}")
        out.append(f"- 重点搜索: {dimension['repo_focus']}")
        out.append("")
        out.append("#### 具体规则")
        out.append("")
        for rule_idx, topic in enumerate(dimension["topics"], start=1):
            out.append(f"##### 规则 {idx}.{rule_idx}: {topic}")
            out.append("")
            out.append(f"- 在 MR 中重点看: {dimension['mr_focus']}")
            out.append(f"- 去源码仓继续看: {dimension['repo_follow_up']}")
            out.append(f"- 直接证据要求: {dimension['direct_evidence']}")
            out.append(f"- 良好信号: {dimension['good_signal']}")
            out.append(f"- 不良信号: {dimension['bad_signal']}")
            out.append(f"- 修复方向: {dimension['repair_pattern']}")
            out.append("")
        out.append("#### 这一维度的高风险反模式")
        out.append("")
        for item in dimension["anti_patterns"]:
            out.append(f"- {item}")
        out.append("")
        out.append("#### 这一维度的审查追问")
        out.append("")
        for item in dimension["questions"]:
            out.append(f"- {item}")
        out.append("")

    out.extend(section("常见误报与越界提醒"))
    for item in profile["false_positives"]:
        out.append(f"- {item}")
    out.append("")

    out.extend(section("Java/Spring 场景化检查清单"))
    for item in profile["java_focus"]:
        out.append(f"- {item}")
    out.append("")

    out.extend(section("典型评审场景"))
    for idx, scenario in enumerate(profile["scenarios"], start=1):
        out.append(f"### 场景 {idx}: {scenario['name']}")
        out.append("")
        out.append(f"- 典型改动: {scenario['change']}")
        out.append(f"- 第一反应不要做什么: {scenario['avoid']}")
        out.append(f"- 应该先去看的代码: {scenario['inspect']}")
        out.append(f"- 结论成立的关键证据: {scenario['evidence']}")
        out.append(f"- 常见错误结论: {scenario['bad_conclusion']}")
        out.append(f"- 更好的修复建议写法: {scenario['repair']}")
        out.append("")

    out.extend(section("结论输出格式"))
    for item in profile["output_contract"]:
        out.append(f"- {item}")
    out.append("")

    out.extend(section("跨文件核对清单"))
    out.extend(
        [
            "- 当前 MR 修改的主文件是否只是入口，真正语义是否落在其他类、配置或转换层？",
            "- 当前字段、状态、枚举、事件名是否在目标分支还有其他读写路径？",
            "- 当前改动是否同步更新了异步消费者、批任务、报表、缓存或投影？",
            "- 当前改动是否引入了新的前置条件、默认值或失败路径？",
            "- 当前改动是否要求调用方、下游或历史数据同步迁移？",
            "- 当前改动是否影响日志、审计、监控、告警或回放工具对链路的理解？",
            "- 当前改动是否会在重试、并发、回滚、补偿、回放场景下产生不同结果？",
            "- 当前改动是否只是把问题从主链路挪到其他任务、事件或缓存链路里？",
            "- 当前改动若上线一半，系统是否仍然保持兼容和可恢复？",
            "- 当前改动若被回滚，是否存在残留状态、脏数据或契约不兼容？",
            "",
        ]
    )

    out.extend(section("修复建议写法"))
    out.extend(
        [
            "- 先写“要改什么”，再写“为什么要这样改”，最后写“如何验证改对了”。",
            "- 修复建议必须指出推荐落点，例如 aggregate、service、repository、migration、consumer、test。",
            "- 若需要联动修改多个文件，要列出主改动点和至少一个关键联动点。",
            "- 若问题涉及兼容性，要明确灰度、补数、回滚或双写双读策略。",
            "- 若问题涉及时间、金额、幂等、权限、缓存、消息等高风险语义，要指出具体样例。",
            "- 不要只写“建议重构”“建议优化”“建议补校验”这类无落点的空话。",
            "- 建议代码修改方案应优先贴近当前项目技术栈和已存在实现，而不是重新发明框架。",
            "- 若证据不足，修复建议应写成“补验证+补保护”，而不是强行给出大改方案。",
            "",
        ]
    )

    out.extend(section("快速口头判断禁令"))
    out.extend(
        [
            "- 禁止只因为看见 import、注解、字段名、目录名就直接下结论。",
            "- 禁止只读单个 diff hunk 就宣称已经理解整条业务链路。",
            "- 禁止把框架偏好、个人习惯或历史写法直接当成缺陷证据。",
            "- 禁止在没有目标分支源码上下文时给高置信结论。",
            "- 禁止把属于其他专家的风险强行写成自己的正式问题。",
            "- 禁止只给“建议优化”却不写具体落点和验证方式。",
            "- 禁止把旧代码已有问题全部记到本次 MR 头上，除非 MR 明确放大了风险。",
            "- 禁止把偶发猜测写成必现缺陷，必须说明触发条件。",
            "- 禁止把测试缺口直接等同于线上缺陷，除非已经有直接业务错误证据。",
            "- 禁止在缺少兼容性证据时随意建议大规模重构或重写。",
            "- 禁止忽略异步链路、回放、补偿、批任务和报表这类非主链路使用点。",
            "- 禁止在修复建议里绕过现有架构和领域边界重新发明一套实现。",
            "",
        ]
    )

    out.extend(section("跨专家协作提示"))
    out.extend(
        [
            "- 当问题同时涉及两个以上领域时，先给出本专家职责内的主结论，再明确建议转交对象。",
            "- 若某条问题的直接根因不在本专家边界内，不要抢结论，只能给出“我观察到的上游/下游信号”。",
            "- 与架构专家协作时，重点提供本领域的直接证据，不要代替其判断总体边界。",
            "- 与正确性专家协作时，重点提供技术语义如何放大业务错误，而不是重写业务定义。",
            "- 与数据库专家协作时，重点提供缓存、消息、事务、契约如何影响数据库链路的补充证据。",
            "- 与安全专家协作时，重点提供实际攻击面或数据边界线索，而不是含糊写成“有风险”。",
            "- 与测试专家协作时，重点指出最需要保护的失败路径和最小可复现样例。",
            "- 若问题已足以由本专家单独闭环，不要为了显得全面而硬拉其他专家。",
            "- 若只是怀疑别的领域可能也有问题，要写成“建议进一步检查”，不要直接替他人定性。",
            "- 在最终结论里，优先保留本专家最强的一到两条问题，而不是平铺大量弱结论。",
            "",
        ]
    )

    out.extend(section("证据引用模板"))
    out.extend(
        [
            "- MR 证据模板: `文件 + hunk + 当前改动前后差异 + 触发条件`。",
            "- 源码仓证据模板: `目标分支文件 + 调用方/被调用方 + 现有实现假设`。",
            "- 风险说明模板: `在什么输入/时序/负载/权限前提下会出错`。",
            "- 修复建议模板: `修改落点 + 关键改法 + 联动文件 + 验证步骤`。",
            "- 若是跨文件问题，必须至少列出“主修改点”和“至少一个受影响上下游文件”。",
            "- 若是兼容性问题，必须列出“旧数据/旧缓存/旧消息/旧调用方”中至少一个实际受影响对象。",
            "- 若是并发问题，必须列出“竞争窗口”或“顺序假设”。",
            "- 若是权限问题，必须列出“攻击者或越权主体”与“被访问对象”。",
            "",
        ]
    )

    out.extend(section("结论自检"))
    out.extend(
        [
            "- 如果把专家名称遮住，这条结论还能看出明确的职责边界吗？",
            "- 如果删掉源码仓上下文，这条结论是否就会失去关键证据？若会，说明引用方式是对的。",
            "- 如果把修复建议交给开发同学，他是否能直接知道下一步要改哪里、补什么验证？",
            "",
        ]
    )

    out.extend(section("合并前最终检查"))
    for item in profile["final_checklist"]:
        out.append(f"- {item}")
    out.append("")

    out.extend(section("术语表"))
    for item in profile["glossary"]:
        out.append(f"- {item}")
    out.append("")

    return "\n".join(out).rstrip() + "\n"


COMMON_OUTPUT = [
    "命中的规范条款: 只列本专家真正命中的维度与规则编号。",
    "问题定性: 在 `direct_defect / risk_hypothesis / test_gap / design_concern` 中选择一种，不要混写。",
    "代码定位: 至少给出文件、方法或类名，以及对应的 diff hunk 或目标分支上下文位置。",
    "证据说明: 先写 MR 片段证据，再写源码仓补充证据，缺一不可时要降级。",
    "影响说明: 说明问题会影响哪类请求、哪条链路、哪种数据或哪类调用。",
    "修复建议: 说明具体改法、推荐落点、需要一起修改的关联代码。",
    "验证建议: 指出需要补的测试、脚本、回放或人工核对步骤。",
    "移交建议: 若问题跨界，明确应该拉哪个专家继续看。",
]


PROFILES: list[dict[str, object]] = [
    {
        "id": "architecture_design",
        "title": "架构与设计专家代码检视规范",
        "role": "Java 企业系统的架构与设计审视专家",
        "mission": "识别分层失真、依赖方向错误、模块边界塌陷和契约设计退化。",
        "sources": [
            "[Google Java Style Guide](https://google.github.io/styleguide/javaguide.html)",
            "[Spring Framework Reference](https://docs.spring.io/spring-framework/reference/)",
            "[Spring Boot Reference Documentation](https://docs.spring.io/spring-boot/docs/current/reference/htmlsingle/)",
            "[Microsoft .NET Microservices: Domain analysis and DDD patterns](https://learn.microsoft.com/azure/architecture/microservices/model/domain-analysis)",
        ],
        "in_scope": [
            "控制器、应用服务、领域服务、仓储、配置类之间的职责边界。",
            "模块依赖方向、共享模型传播、跨上下文引用和循环依赖。",
            "接口契约是否稳定、是否把基础设施细节泄漏到上层。",
            "扩展点、装配方式、开关配置与运行时策略的设计质量。",
        ],
        "out_of_scope": [
            "不负责判断 SQL 是否走索引，这属于数据库专家。",
            "不负责判断缓存 TTL 是否合适，这属于 Redis 或性能专家。",
            "不负责判断认证授权漏洞是否成立，这属于安全专家。",
            "不负责判断测试覆盖是否充分，这属于测试专家。",
        ],
        "handoff": [
            "看到事务边界、锁、迁移脚本或 ORM 映射问题时移交数据库专家。",
            "看到缓存一致性、缓存击穿、序列化问题时移交 Redis 专家。",
            "看到权限、鉴权、敏感数据暴露时移交安全专家。",
            "看到幂等投递、顺序消费、死信队列时移交 MQ 专家。",
        ],
        "repo_queries": [
            "搜索控制器是否直接依赖 repository、mapper 或 SQL 相关类。",
            "搜索 application/service/usecase 是否承担了领域规则之外的协议装配。",
            "搜索同名 DTO、VO、Entity、Aggregate 是否在多个层之间被直接复用。",
            "搜索 configuration、factory、assembler、orchestrator 是否吸收了业务规则。",
            "搜索模块之间是否存在双向依赖或 shared 包持续膨胀。",
        ],
        "evidence": [
            "至少给出一个 diff hunk 证据和一个目标分支上下文证据。",
            "不能只因为看见 import 就判断架构失真，必须证明调用链真的跨层。",
            "如果只是设计偏好不同但没有造成边界泄漏，只能作为 design_concern。",
            "需要指出是哪个层向哪个层泄漏、泄漏了什么、后果是什么。",
        ],
        "dimensions": [
            {
                "name": "分层边界",
                "objective": "确认接口层、应用层、领域层、基础设施层各自只承担本层职责。",
                "repo_focus": "controller/service/usecase/domain/repository/config 等命名空间与调用链。",
                "mr_focus": "新增依赖、跨层调用、装配逻辑和 DTO/Entity 直接穿透。",
                "repo_follow_up": "同名服务、同类控制器、相邻模块的装配方式以及既有调用边界。",
                "direct_evidence": "需要看到具体类或方法跨层依赖，不能只凭目录名推断。",
                "good_signal": "控制器只做协议适配，应用服务只编排流程，领域对象只表达业务规则。",
                "bad_signal": "控制器直接写数据库、应用服务拼 SQL、领域层持有框架或 HTTP 细节。",
                "repair_pattern": "把协议适配、业务编排、领域规则、基础设施调用拆回各自层次。",
                "topics": [
                    "Controller 只做协议适配，不直接落 repository 或 mapper 细节",
                    "Application Service 只做流程编排，不吞并领域规则",
                    "Domain Service 不感知 HTTP、消息协议或框架注解语义",
                    "Repository 只表达持久化意图，不承载业务分支判断",
                ],
                "anti_patterns": [
                    "接口层为了省事直接 new 基础设施客户端并拼业务参数。",
                    "应用服务把十几个 if/else 业务规则塞在事务方法里。",
                    "领域对象暴露 JSON、HTTP、MQ、Redis 等技术细节。",
                    "一个 orchestrator 同时做路由、校验、规则判断和数据组装。",
                ],
                "questions": [
                    "如果控制器被换成消息入口，这段业务规则是否还能复用？",
                    "如果底层持久化实现改变，上层是否必须跟着改方法签名？",
                    "当前规则应属于应用层流程，还是领域层不变量？",
                    "这段映射代码是否只在接口层或基础设施层出现一次？",
                ],
            },
            {
                "name": "依赖方向",
                "objective": "确认高层依赖抽象，低层实现不反向污染高层模块。",
                "repo_focus": "模块依赖图、端口接口、shared 包、adapter 包和跨模块 import。",
                "mr_focus": "新增 import、跨模块调用、shared 抽取和具体实现注入方式。",
                "repo_follow_up": "被引入类型的定义位置、依赖反向路径、模块公共接口和实现类分布。",
                "direct_evidence": "需要证明依赖方向被破坏，而不是单纯存在引用关系。",
                "good_signal": "上层依赖接口或抽象，低层实现可替换且不会改变上层契约。",
                "bad_signal": "业务模块直接依赖技术实现、双向引用、shared 包充当垃圾场。",
                "repair_pattern": "提炼端口接口、收口 shared 模型、将具体实现下沉到适配器层。",
                "topics": [
                    "上层依赖抽象而不是具体实现类",
                    "避免跨 bounded context 直接引用内部模型",
                    "避免 shared 包无限膨胀为隐式公共领域",
                    "禁止通过工具类或静态入口偷偷跨层耦合",
                ],
                "anti_patterns": [
                    "领域服务直接 import JDBC、RedisTemplate 或 HTTP client。",
                    "两个模块通过共享 entity 互相调用内部字段。",
                    "shared/common 下沉积了 DTO、entity、mapper 和业务常量。",
                    "为了绕过依赖关系，用静态单例或全局注册表拿对象。",
                ],
                "questions": [
                    "若移除当前具体实现，业务模块是否仍能编译与运行？",
                    "当前共享类型是否真的是共享语言，还是临时偷懒？",
                    "是否存在 A 依赖 B，B 又经工具类反向依赖 A？",
                    "是否能用端口接口或事件替代当前硬连接？",
                ],
            },
            {
                "name": "契约设计",
                "objective": "确认 API、事件、命令和返回值契约稳定且职责清晰。",
                "repo_focus": "request/response DTO、event payload、command object、mapper 与 validator。",
                "mr_focus": "字段增删改、默认值变化、nullability、版本兼容、时间金额精度。",
                "repo_follow_up": "所有序列化边界、使用该 DTO 的调用方、事件消费者和下游协议转换点。",
                "direct_evidence": "需要证明字段语义、兼容性或精度被改坏，而不是仅仅字段变了。",
                "good_signal": "契约字段职责单一，兼容策略明确，null/默认值有统一语义。",
                "bad_signal": "把内部实现细节、缓存字段或技术字段直接暴露给外部契约。",
                "repair_pattern": "补兼容字段、显式版本、统一默认值语义并调整 mapper/validator。",
                "topics": [
                    "API DTO 不直接暴露内部实体结构",
                    "事件 payload 的兼容策略和版本语义明确",
                    "时间、金额、状态字段的空值和默认值语义稳定",
                    "Mapper 的拥有者清晰，不在多层重复转换",
                ],
                "anti_patterns": [
                    "只改输出 DTO，不同步 transformer、consumer 和测试。",
                    "新增字段后依赖旧默认值，却没有写兼容说明。",
                    "不同层各自手写映射逻辑，导致契约语义漂移。",
                    "用同一个 DTO 兼顾读接口、写接口和内部持久化。",
                ],
                "questions": [
                    "新增字段是否要求所有调用方同时升级？",
                    "null、缺省、空字符串、零值在当前契约里是否可区分？",
                    "事件消费者是否仍能读取新 payload？",
                    "是否存在两个 mapper 对同一字段给出不同语义？",
                ],
            },
            {
                "name": "模块化与演进性",
                "objective": "确认当前改动不会让未来扩展、替换实现或按模块下线变得困难。",
                "repo_focus": "feature 模块边界、插件点、策略接口、开关配置、装配入口。",
                "mr_focus": "新增开关、条件分支、可选实现、feature flag、模块拼装逻辑。",
                "repo_follow_up": "同类 feature 是否有统一扩展点，替换实现是否需要改多处。",
                "direct_evidence": "需要指出扩展必须改动多个模块或必须碰底层实现的具体位置。",
                "good_signal": "新增能力通过策略、配置或适配器扩展，现有模块最小改动。",
                "bad_signal": "每加一个场景就修改多个模块、多层 switch/case、复制一套分支。",
                "repair_pattern": "抽策略接口、统一 feature registration、把开关下沉到装配层。",
                "topics": [
                    "新增能力应优先走策略扩展点而不是复制分支",
                    "feature flag 应集中管理而非散落 if/else",
                    "替换实现不应要求改多个高层模块",
                    "模块装配入口要稳定，避免多点注册和重复 wiring",
                ],
                "anti_patterns": [
                    "一个新场景需要同时改 controller、service、repository、config 四层分支。",
                    "不同模块各自定义同一开关，导致状态不一致。",
                    "想切换实现时需要全局搜索十几个 if/else。",
                    "注册逻辑分散在多个 AutoConfiguration 或工厂类里。",
                ],
                "questions": [
                    "如果再来一个第三种实现，当前结构是否还能承受？",
                    "是否能通过配置注册而不是硬编码枚举？",
                    "同类 feature 的装配方式是否保持一致？",
                    "移除这块能力时是否需要清理很多散落代码？",
                ],
            },
            {
                "name": "编排与事务边界",
                "objective": "确认流程编排、事务边界和副作用控制没有被揉成难以维护的大块逻辑。",
                "repo_focus": "transactional 方法、workflow/orchestrator、事件发布、外部调用顺序。",
                "mr_focus": "事务注解变化、外部调用放入事务、事件发布位置、副作用顺序调整。",
                "repo_follow_up": "上下游调用链、事件/消息发布点、失败回滚策略和补偿实现。",
                "direct_evidence": "需要证明事务与副作用顺序会导致不一致、放大耦合或难以补偿。",
                "good_signal": "事务只包围必须一致的数据写操作，外部副作用有明确边界。",
                "bad_signal": "一个大事务包住数据库、HTTP、MQ、缓存刷新和复杂分支。",
                "repair_pattern": "缩小事务范围、拆分流程节点、引入 outbox/事件驱动或补偿机制。",
                "topics": [
                    "事务内只保留必须原子化的数据变化",
                    "外部副作用不要直接塞进核心事务方法",
                    "事件发布时机要与提交语义匹配",
                    "复杂 workflow 要拆成可理解的步骤而非巨型方法",
                ],
                "anti_patterns": [
                    "事务方法内先调远端接口，再写库，再刷缓存。",
                    "事件在事务未提交前发布，消费者读到脏数据。",
                    "一个 orchestrator 方法超过数十行还承担重试和降级分支。",
                    "补偿逻辑散落在 catch 块里且无法单测。",
                ],
                "questions": [
                    "这个外部调用真的需要与数据库写入同事务吗？",
                    "如果事务回滚，已经发出去的事件或请求怎么办？",
                    "当前流程是否可以按状态机或步骤拆开？",
                    "失败后的一致性恢复责任落在谁身上？",
                ],
            },
        ],
        "false_positives": [
            "不要只因为目录名出现 domain 就自动认为它是领域层，必须看实际职责。",
            "不要把风格偏好当成架构问题；只有出现边界泄漏、依赖倒置失败或契约退化时才升级。",
            "不要因为文件大就下结论，必须指出为什么这会造成职责混杂或扩展困难。",
            "不要代替数据库、安全、性能专家去给出技术细节判定。",
            "不要把“未来也许会扩展”当成充分理由，必须指出已经出现的设计债位置。",
        ],
        "java_focus": [
            "检查 Spring MVC Controller 是否只做参数绑定、鉴权入口和返回值包装。",
            "检查 Service/UseCase 是否只编排事务边界、领域对象和仓储调用。",
            "检查 Domain 模型是否避免依赖 Spring、Jackson、JPA 之外的协议细节。",
            "检查 Configuration、AutoConfiguration、Factory 是否只承担装配与注册。",
            "检查 MapStruct/Assembler/Converter 是否只有唯一拥有者。",
            "检查 EventPublisher、ApplicationEvent、Outbox 触发点是否位于正确层次。",
            "检查 Feign/WebClient/RestTemplate 是否通过适配器封装，而不是直接渗到业务层。",
            "检查 feature flag 是否有统一配置入口与下线策略。",
        ],
        "scenarios": [
            {
                "name": "控制器里新增 repository 调用",
                "change": "MR 在 Controller 中直接查询数据库并拼接返回对象。",
                "avoid": "不要只写“分层不清晰”，而不说明它破坏了什么。",
                "inspect": "去搜同类 Controller、UseCase 和 Mapper 的既有分工。",
                "evidence": "证明控制器承担了业务规则或持久化细节，而非单纯协议转换。",
                "bad_conclusion": "“Controller 里不能有任何逻辑”这种绝对化判断。",
                "repair": "建议把查询与规则判断下沉到应用服务，控制器只保留协议适配。",
            },
            {
                "name": "新增 shared DTO 被多个上下文复用",
                "change": "MR 新建 common DTO 并被两个模块直接 import。",
                "avoid": "不要因为看见 shared 就立刻否定，先确认它是不是共享语言。",
                "inspect": "去看两个模块是否共享同一业务概念，还是临时偷懒。",
                "evidence": "需要看到字段语义被不同模块解释成不同含义的证据。",
                "bad_conclusion": "“公共 DTO 一律不允许”这种武断结论。",
                "repair": "若不是共享语言，建议拆成各自上下文的契约并用 mapper 转换。",
            },
            {
                "name": "事务方法里新增远端调用",
                "change": "MR 在 @Transactional 方法里加入 Feign/WebClient 调用。",
                "avoid": "不要直接把它归为性能问题，要先看架构与一致性边界。",
                "inspect": "搜索事务范围、事件发布时机和失败补偿代码。",
                "evidence": "说明为什么外部副作用进入事务会导致边界混乱或补偿困难。",
                "bad_conclusion": "“事务里绝不能有任何远端调用”而不分析场景。",
                "repair": "建议缩小事务或拆出 outbox/异步补偿边界。",
            },
            {
                "name": "通过配置类写业务规则",
                "change": "MR 在 Configuration 或 Bean Factory 中写入分支业务判断。",
                "avoid": "不要只批评代码位置难看，要指出装配层与业务层混在一起。",
                "inspect": "查看配置类是否持有业务对象、状态常量或条件组合。",
                "evidence": "说明为什么这会让测试、替换实现或开关管理变难。",
                "bad_conclusion": "“配置类不应该有 if”这种表层意见。",
                "repair": "建议把业务选择抽成策略接口，把配置类只保留 wiring。",
            },
            {
                "name": "事件消费者直接写业务核心表",
                "change": "MR 的消息监听器里直接修改多个领域对象状态。",
                "avoid": "不要替 MQ 专家判断投递语义，先看职责边界。",
                "inspect": "查看监听器是否直接承担领域规则和事务编排。",
                "evidence": "指出监听器已经变成新的应用服务入口但缺少明确边界。",
                "bad_conclusion": "“监听器就不该写库”这种过度概括。",
                "repair": "建议监听器只做消息适配，把领域更新交给应用服务或领域服务。",
            },
            {
                "name": "新增 feature 需要改多个模块的 switch",
                "change": "MR 为新场景同时改多个模块里的枚举分支。",
                "avoid": "不要只从代码重复角度批评，这更像扩展点设计问题。",
                "inspect": "搜索所有 switch/case、strategy registry、factory 装配点。",
                "evidence": "证明系统每扩一个场景都必须改动多处固定分支。",
                "bad_conclusion": "“有 switch 就一定不行”这种机械规则。",
                "repair": "建议提炼统一策略注册点，避免扩展时触碰多个高层模块。",
            },
        ],
        "output_contract": COMMON_OUTPUT,
        "final_checklist": [
            "是否明确指出了被破坏的边界，而不是只写“设计不够优雅”？",
            "是否给出了跨层依赖、共享模型或契约退化的直接证据？",
            "是否避免替数据库、安全、性能专家下技术细节结论？",
            "是否说明了更合理的落层位置和拆分方式？",
            "是否指出关联 mapper、DTO、配置或事件契约需要一起调整？",
            "是否把证据不足的项降级为 design_concern 或 risk_hypothesis？",
        ],
        "glossary": [
            "分层边界: 协议适配、流程编排、领域规则、基础设施实现之间的职责界线。",
            "依赖方向: 高层依赖抽象、低层实现依赖高层定义的端口。",
            "共享语言: 多个上下文都能接受且语义稳定的公共概念。",
            "装配层: 负责把实现与抽象连接起来的配置或工厂层。",
            "扩展点: 新增场景时尽量只新增实现而不是修改大量现有代码的位置。",
            "边界泄漏: 某层开始持有不该属于本层的规则、数据或技术细节。",
        ],
    },
]


def duplicate_from(base: dict[str, object], **updates: object) -> dict[str, object]:
    clone = dict(base)
    clone.update(updates)
    return clone


def with_common_lists(
    *,
    expert_id: str,
    title: str,
    role: str,
    mission: str,
    sources: list[str],
    in_scope: list[str],
    out_scope: list[str],
    handoff: list[str],
    repo_queries: list[str],
    evidence: list[str],
    dimensions: list[dict[str, object]],
    false_positives: list[str],
    java_focus: list[str],
    scenarios: list[dict[str, str]],
    final_checklist: list[str],
    glossary: list[str],
) -> dict[str, object]:
    return {
        "id": expert_id,
        "title": title,
        "role": role,
        "mission": mission,
        "sources": sources,
        "in_scope": in_scope,
        "out_of_scope": out_scope,
        "handoff": handoff,
        "repo_queries": repo_queries,
        "evidence": evidence,
        "dimensions": dimensions,
        "false_positives": false_positives,
        "java_focus": java_focus,
        "scenarios": scenarios,
        "output_contract": COMMON_OUTPUT,
        "final_checklist": final_checklist,
        "glossary": glossary,
    }


def auto_dimension(
    *,
    name: str,
    objective: str,
    repo_focus: str,
    topics: list[str],
    anti_patterns: list[str],
    questions: list[str],
    good_signal: str,
    bad_signal: str,
    repair_pattern: str,
) -> dict[str, object]:
    return {
        "name": name,
        "objective": objective,
        "repo_focus": repo_focus,
        "mr_focus": f"与“{name}”直接相关的新增字段、控制分支、配置、调用顺序和副作用变化。",
        "repo_follow_up": f"围绕“{name}”在目标分支继续检索调用方、被调用方、同类实现和历史约定。",
        "direct_evidence": f"需要证明“{name}”在真实运行链路中会出错、退化或失真，不能只凭经验下结论。",
        "good_signal": good_signal,
        "bad_signal": bad_signal,
        "repair_pattern": repair_pattern,
        "topics": topics,
        "anti_patterns": anti_patterns,
        "questions": questions,
    }


PROFILES.extend(
    [
        with_common_lists(
            expert_id="correctness_business",
            title="正确性与业务规则专家代码检视规范",
            role="Java 企业系统的业务正确性与边界条件审视专家",
            mission="识别业务语义偏差、状态流转错误、边界条件缺失与时间金额等高风险正确性问题。",
            sources=[
                "[Spring Framework Reference](https://docs.spring.io/spring-framework/reference/)",
                "[Spring Boot Reference Documentation](https://docs.spring.io/spring-boot/docs/current/reference/htmlsingle/)",
                "[Jackson Databind Documentation](https://github.com/FasterXML/jackson-databind)",
                "[Oracle Java Time API](https://docs.oracle.com/javase/8/docs/api/java/time/package-summary.html)",
            ],
            in_scope=[
                "业务状态流转、输入输出语义、边界条件、空值/默认值语义。",
                "时间、时区、金额、精度、幂等键、去重键和业务唯一性约束。",
                "transformer、assembler、service、workflow 之间的语义一致性。",
                "跨文件链路中的语义偏差，例如 DTO 变了但 transformer 或 consumer 没跟上。",
            ],
            out_scope=[
                "不负责索引、执行计划和迁移锁，这属于数据库专家。",
                "不负责缓存 TTL、热点 key 和内存策略，这属于 Redis 或性能专家。",
                "不负责认证授权漏洞定性，这属于安全专家。",
                "不负责测试框架组织方式，这属于测试专家。",
            ],
            handoff=[
                "看到事务隔离、锁等待、schema 演进时移交数据库专家。",
                "看到权限、租户隔离、敏感字段暴露时移交安全专家。",
                "看到性能退化、超时、重试放大时移交性能专家。",
                "看到缓存命中语义与数据一致性问题时移交 Redis 专家。",
            ],
            repo_queries=[
                "搜索同名字段在 request/response/entity/transformer 中的全链路落点。",
                "搜索状态枚举、状态机、命令处理器、workflow 和事件消费者。",
                "搜索时间字段、金额字段、时区转换、null/default 处理逻辑。",
                "搜索唯一键、幂等键、去重逻辑和历史回放入口。",
                "搜索调用方是否假设了旧的返回结构或默认值语义。",
            ],
            evidence=[
                "必须同时指出“输入如何变化”和“输出或副作用如何变化”。",
                "如果是跨文件问题，至少给出两处代码证据而不是只看 diff 一处。",
                "不要因为字段新增就自动定性错误，必须说明原有业务语义被破坏在哪里。",
                "时间、金额、状态类问题必须说明边界条件和受影响场景。",
            ],
            dimensions=[
                {
                    "name": "状态流转",
                    "objective": "确认状态机、分支流程和生命周期变更没有改坏业务语义。",
                    "repo_focus": "status enum、workflow、handler、transition 方法和事件处理器。",
                    "mr_focus": "状态判断、分支顺序、短路条件、失败恢复和终态变更。",
                    "repo_follow_up": "所有写状态的入口、读状态的下游和依赖该状态的查询逻辑。",
                    "direct_evidence": "需要证明某个输入会走错分支、写错状态或漏掉终态。",
                    "good_signal": "状态变迁有单一入口，前置条件、失败路径和终态清晰一致。",
                    "bad_signal": "多个地方各自改状态、同一状态名在不同链路含义不一致。",
                    "repair_pattern": "收口状态变更入口，补前置条件和终态校验，统一状态语义。",
                    "topics": [
                        "状态迁移前置条件是否仍然成立",
                        "失败路径是否会回到正确状态或中间态",
                        "重复请求是否会把状态推进到错误终态",
                        "补偿或重放是否会造成状态回退/前跳异常",
                    ],
                    "anti_patterns": [
                        "在多个 service 各自 setStatus，没人拥有最终语义。",
                        "新增状态后只改写入逻辑，不改读取分支。",
                        "同一异常在不同位置映射到不同业务状态。",
                        "幂等重放把已完成状态重新推进一次。",
                    ],
                    "questions": [
                        "同一输入重试两次，状态是否仍然正确？",
                        "异常和取消路径是否落到明确可恢复状态？",
                        "查询侧是否知道新增状态该如何解释？",
                        "事件重放会不会造成重复状态推进？",
                    ],
                },
                {
                    "name": "输入输出语义",
                    "objective": "确认字段语义、默认值和对象转换没有让业务含义悄悄漂移。",
                    "repo_focus": "request/response DTO、assembler、transformer、mapper 和序列化边界。",
                    "mr_focus": "字段增删改、nullability、默认值、枚举映射、日期金额转换。",
                    "repo_follow_up": "该字段在输出层、持久化层、缓存层和消费方的落点。",
                    "direct_evidence": "需要证明字段含义变化会影响调用方、消费者或下游流程。",
                    "good_signal": "字段语义在各层保持一致，缺省与空值处理有统一约定。",
                    "bad_signal": "DTO 改了、transformer 没改，或者不同层对空值解释不同。",
                    "repair_pattern": "统一字段语义，补 transformer/mapper，并更新兼容逻辑。",
                    "topics": [
                        "新增字段是否同步出现在 transformer 和输出 DTO",
                        "null、空字符串、零值是否被正确区分",
                        "枚举映射是否覆盖旧值和未知值",
                        "金额和精度转换是否保持原有业务语义",
                    ],
                    "anti_patterns": [
                        "只给类型加了 nullable，却没处理旧缓存或旧序列化数据。",
                        "输出字段新增后仍沿用旧 transformer，导致字段始终空。",
                        "把金额从 decimal 变为 double 却没有精度说明。",
                        "把 Optional 当成业务含义而不是边界表达。",
                    ],
                    "questions": [
                        "旧数据回放时，这个字段会是什么值？",
                        "消费方是否把空值当成“未设置”还是“明确为空”？",
                        "输出对象的新增字段会不会触发前端或下游默认行为变化？",
                        "金额/比例/时间字段是否还满足原有精度和舍入规则？",
                    ],
                },
                {
                    "name": "时间与幂等",
                    "objective": "确认时间窗口、时区、去重与幂等逻辑没有被改坏。",
                    "repo_focus": "Instant/LocalDateTime/ZonedDateTime、dedup key、request id、schedule 逻辑。",
                    "mr_focus": "创建/更新时间、过期时间、重试、定时任务、去重键和 replay。",
                    "repo_follow_up": "所有读写时间字段的地方、去重表、幂等存储和消费端校验。",
                    "direct_evidence": "需要说明在什么时间窗口或重复请求场景下会出现错误。",
                    "good_signal": "时间语义统一，幂等键稳定，重复投递或重试不会产生副作用偏差。",
                    "bad_signal": "时间字段加了新语义却没处理旧值，重试会重复写入或重复通知。",
                    "repair_pattern": "统一时间类型和时区边界，显式幂等键并补重试场景测试。",
                    "topics": [
                        "createdAt/updatedAt 等字段在全链路是否同步更新",
                        "时区转换和显示时间是否与业务定义一致",
                        "幂等键是否包含真正区分业务动作的维度",
                        "重试/回放是否会重复产生外部副作用",
                    ],
                    "anti_patterns": [
                        "把 LocalDateTime 直接序列化跨时区传输。",
                        "新增时间字段只改 schema，不改 transformer 和排序逻辑。",
                        "用自增 id 当幂等键，跨系统重试完全不可靠。",
                        "去重窗口只在内存里，服务重启后立即失效。",
                    ],
                    "questions": [
                        "旧记录没有这个时间字段时，排序和展示会怎样？",
                        "用户所在时区和系统默认时区不一致时会怎样？",
                        "相同业务动作跨节点重试时能否稳定去重？",
                        "回放历史消息时是否会重复触发通知或记账？",
                    ],
                },
                {
                    "name": "关联链路一致性",
                    "objective": "确认 service、transformer、consumer、query side 对同一业务语义理解一致。",
                    "repo_focus": "service output、query builder、consumer handler、read model updater。",
                    "mr_focus": "多文件同一字段或同一语义的联动修改情况。",
                    "repo_follow_up": "搜索同名字段、同名方法、同一业务术语的上下游使用点。",
                    "direct_evidence": "需要证明至少一处上下游仍在依赖旧语义。",
                    "good_signal": "同一业务术语在 service、query、consumer 中都一致更新。",
                    "bad_signal": "service 改了、read model 没改，或者 transformer 仍沿用旧契约。",
                    "repair_pattern": "补齐跨文件联动修改，并用高层语义而非字段表象解释修复。",
                    "topics": [
                        "service 层的输出变更是否同步到 read model",
                        "query side 的排序/过滤是否适配新字段语义",
                        "异步消费者是否仍按旧 payload 解释业务含义",
                        "聚合结果对象是否保持对外一致行为",
                    ],
                    "anti_patterns": [
                        "只改写入链路，不改读取链路。",
                        "read model 仍假设字段永远非空。",
                        "consumer 看到新字段却依旧从旧字段推导业务状态。",
                        "service 和 transformer 对字段命名一致，但语义不一致。",
                    ],
                    "questions": [
                        "如果只更新写入侧，不更新读取侧，哪个页面或接口会错？",
                        "异步链路消费旧消息时是否仍然安全？",
                        "是否存在多个 transformer 各自决定业务含义？",
                        "缓存或投影是否会把旧语义保留很久？",
                    ],
                },
                {
                    "name": "边界条件",
                    "objective": "确认空集合、缺失数据、重复数据和异常输入不会把业务推向错误结果。",
                    "repo_focus": "guard clause、optional 处理、异常映射、空集合/空对象逻辑。",
                    "mr_focus": "新增字段、边界判断、异常分支、默认值和 fallback 行为。",
                    "repo_follow_up": "调用方是否依赖异常、空值或 fallback 的原有行为。",
                    "direct_evidence": "需要指出具体输入样本会怎样触发错误结果。",
                    "good_signal": "边界输入得到明确、可预测且与业务定义一致的结果。",
                    "bad_signal": "依赖框架默认值、依赖 null 传播或吞掉异常假装成功。",
                    "repair_pattern": "补显式 guard、明确异常语义、区分空值与缺失并增加测试。",
                    "topics": [
                        "空集合和无数据场景的返回语义是否稳定",
                        "异常是否被错误吞掉并映射成成功结果",
                        "重复输入是否会产生重复业务结果",
                        "fallback 是否掩盖了真实业务失败",
                    ],
                    "anti_patterns": [
                        "catch 住所有异常后返回空列表，调用方误以为成功。",
                        "为空时走默认值，但默认值本身有业务含义。",
                        "重复提交时虽然不报错，但副作用已经发生两次。",
                        "fallback 逻辑绕过关键校验，导致结果看似可用其实错误。",
                    ],
                    "questions": [
                        "没有任何数据时，调用方希望看到什么？",
                        "异常和空结果在当前业务里是否应该区分？",
                        "重复输入是否需要返回同一结果还是拒绝？",
                        "fallback 是为了可用性，还是在掩盖真实错误？",
                    ],
                },
            ],
            false_positives=[
                "不要把设计偏好问题误报成业务正确性问题；必须说明业务结果会错在哪里。",
                "不要仅凭字段改名就下结论，必须确认语义、默认值或调用方行为真的变化了。",
                "不要因为看见时间字段就自动上升到幂等问题，必须证明重试或回放场景会错。",
                "不要代替数据库专家评论索引和锁，也不要代替测试专家评价测试架构。",
                "不要把单纯的代码重复视作正确性缺陷，除非它导致不同分支给出不同业务结果。",
            ],
            java_focus=[
                "检查 Spring MVC / GraphQL / RPC 输入对象在校验后是否仍满足业务前置条件。",
                "检查 Service / Handler / Workflow 是否同时修改了状态、投影、通知和缓存。",
                "检查 Jackson 序列化、枚举解析、日期格式化是否影响业务值。",
                "检查 Java time API 的使用是否明确 Instant、LocalDate、LocalDateTime、ZoneId 边界。",
                "检查金额与比例是否使用 BigDecimal 且有统一舍入策略。",
                "检查重试器、消息消费器、定时任务和回放入口是否共享幂等判断。",
                "检查 transformer/mapper 是否随同 DTO 变更同步更新。",
            ],
            scenarios=[
                {
                    "name": "DTO 加了时间字段但 transformer 未同步",
                    "change": "MR 给输出 DTO 增加 createdAt/updatedAt 字段。",
                    "avoid": "不要只说“字段没赋值”，要说明会影响哪些查询、排序或展示语义。",
                    "inspect": "搜索 transformer、read model、排序逻辑和前端/下游消费点。",
                    "evidence": "给出 DTO 字段新增与 transformer 仍返回旧结构的双重证据。",
                    "bad_conclusion": "“少了个字段问题不大”这种轻描淡写判断。",
                    "repair": "建议补齐 transformer 和相关投影逻辑，并补字段存在性的回归测试。",
                },
                {
                    "name": "状态机多了新分支但失败路径没更新",
                    "change": "MR 新增一个业务状态或异常状态。",
                    "avoid": "不要只盯成功路径，要看取消、回滚、重试和查询展示。",
                    "inspect": "搜索所有读取该状态的页面、任务和消费者。",
                    "evidence": "证明失败后会落到错误状态或查询层看不懂新状态。",
                    "bad_conclusion": "“新增状态没问题，只是枚举多一个”这种表面结论。",
                    "repair": "建议补齐状态读取分支、失败映射和重试/重放测试。",
                },
                {
                    "name": "幂等键维度不足",
                    "change": "MR 在重试或消费链路中复用了旧幂等键。",
                    "avoid": "不要空泛说“可能重复”，要指出哪个维度缺失。",
                    "inspect": "查看 request id、业务主键、时间窗口和去重表结构。",
                    "evidence": "给出两个会被错误视为同一次请求或同一次消费的样例。",
                    "bad_conclusion": "“用了 requestId 就一定幂等”这种错误经验。",
                    "repair": "建议把真正区分业务动作的字段纳入幂等键，并补重复调用测试。",
                },
                {
                    "name": "默认值掩盖业务缺失",
                    "change": "MR 给新字段加了默认值或 fallback。",
                    "avoid": "不要把所有默认值都打成错误，要确认默认值是否改变业务含义。",
                    "inspect": "查看调用方是否把默认值解释成真实业务状态。",
                    "evidence": "证明缺失数据会被误判为真实结果，而不是安全降级。",
                    "bad_conclusion": "“默认值不好看”这种风格化意见。",
                    "repair": "建议显式区分 unknown/not-set/defaulted 三种语义，并补转换逻辑。",
                },
                {
                    "name": "时间类型改动引发排序/展示错误",
                    "change": "MR 将 LocalDateTime、Instant 或字符串时间相互转换。",
                    "avoid": "不要只谈风格，必须指出时区或排序边界会怎么错。",
                    "inspect": "查看排序、过滤、分页和 UI 展示格式转换。",
                    "evidence": "说明在跨时区或旧数据回放时会如何偏移。",
                    "bad_conclusion": "“都是时间字段，应该没差”这种粗糙判断。",
                    "repair": "建议统一时间基准，明确持久化与展示层的转换边界。",
                },
                {
                    "name": "写入链路更新了，读取链路没更新",
                    "change": "MR 修改 service 输出或存储字段，但 read model 仍依赖旧结构。",
                    "avoid": "不要只看本文件，要把 query side 一并搜出来。",
                    "inspect": "查看 projection builder、查询接口和缓存投影。",
                    "evidence": "证明用户读到的结果与写入后的真实业务状态不一致。",
                    "bad_conclusion": "“写入成功就说明改动没问题”这种片面结论。",
                    "repair": "建议同步调整读取链路、缓存刷新和回归测试。",
                },
            ],
            final_checklist=[
                "是否明确指出了哪种输入、状态或边界条件会出错？",
                "是否至少给出两处代码证据来证明跨文件语义偏差？",
                "是否把时间/金额/幂等问题说清楚到具体字段和具体窗口？",
                "是否避免越界去评价数据库、缓存或测试框架问题？",
                "是否给出了明确的修复动作和要补的验证用例？",
            ],
            glossary=[
                "业务语义: 代码字段或状态在真实业务里的含义，而不是字面名词。",
                "幂等: 同一业务动作重复执行不会产生额外副作用。",
                "边界条件: 空值、重复值、旧数据、异常值、时区差异等非主路径输入。",
                "投影: 为查询或展示准备的读模型，不一定等于写模型。",
                "语义漂移: 不同层对同一字段或状态逐渐给出不同解释。",
            ],
        ),
    ]
)

PROFILES.extend(
    [
        with_common_lists(
            expert_id="performance_reliability",
            title="性能与可靠性专家代码检视规范",
            role="关注延迟、吞吐、资源、超时、重试与降级的性能可靠性专家",
            mission="识别高负载、异常放大、资源耗尽和不稳定链路中的性能与可靠性问题。",
            sources=[
                "[Spring Boot Actuator Reference](https://docs.spring.io/spring-boot/docs/current/reference/html/actuator.html)",
                "[Resilience4j Documentation](https://resilience4j.readme.io/)",
                "[Java Concurrency in Practice summary](https://jcip.net/)",
            ],
            in_scope=[
                "超时、重试、限流、熔断、降级、背压和资源释放。",
                "热点路径中的对象创建、阻塞 IO、并发竞争和线程池使用。",
                "缓存失效放大、批量任务与在线链路相互影响、异常风暴。",
                "稳定性保护与可观测性是否足够支撑问题定位和降级。",
            ],
            out_scope=[
                "不负责业务语义正确性，这属于正确性专家。",
                "不负责数据库执行计划细节，这属于数据库专家。",
                "不负责 Redis key/TTL 细节，这属于 Redis 专家。",
                "不负责 MQ 投递语义，这属于 MQ 专家。",
            ],
            handoff=[
                "看到索引、SQL 和事务细节时移交数据库专家。",
                "看到缓存键设计和 TTL 语义时移交 Redis 专家。",
                "看到消息堆积与幂等等语义时移交 MQ 专家。",
                "看到业务状态错误时移交正确性专家。",
            ],
            repo_queries=[
                "搜索 timeout、retry、circuit breaker、rate limit、bulkhead。",
                "搜索线程池、异步执行器、阻塞调用、sleep、同步锁和批量任务。",
                "搜索热点接口的缓存、回退、默认值和异常处理。",
                "搜索 metrics、日志、告警、trace 是否覆盖关键退化点。",
                "搜索线上任务与离线任务是否共享资源池和数据库表。",
            ],
            evidence=[
                "必须说明在什么负载、什么异常或什么重试场景下会退化。",
                "不能只说“可能慢”，要指出阻塞、放大或资源耗尽链路。",
                "可靠性问题要说明失败后系统如何继续恶化或失去恢复能力。",
                "若没有高负载证据，只能降级为风险提示。",
            ],
            dimensions=[
                auto_dimension(
                    name="超时与重试",
                    objective="确认超时、重试和回退策略不会放大故障。",
                    repo_focus="client timeout、retry、fallback、circuit breaker、scheduler。",
                    topics=[
                        "超时边界是否存在且与上游 SLA 匹配",
                        "重试是否会重复放大写操作或远端压力",
                        "fallback 是否掩盖根因并返回错误语义",
                        "重试、熔断和限流是否协调而非互相打架",
                    ],
                    anti_patterns=[
                        "没有超时，线程无限等待远端。",
                        "写操作默认重试三次且无去重。",
                        "fallback 返回看似成功的默认值，掩盖真实失败。",
                        "每一层都自带重试，最终指数放大。",
                    ],
                    questions=[
                        "超时到达后资源是否及时释放？",
                        "重试是否作用在幂等操作上？",
                        "fallback 的返回值对业务是否仍然安全？",
                        "调用链总重试次数是否可控？",
                    ],
                    good_signal="超时明确、重试克制、fallback 安全且可观测。",
                    bad_signal="异常时所有层一起重试，越失败越忙。",
                    repair_pattern="收口超时与重试策略，给写链路加幂等保护并限制总放大量。",
                ),
                auto_dimension(
                    name="资源与并发",
                    objective="确认线程、连接、锁和对象创建不会在负载下失控。",
                    repo_focus="thread pool、executor、synchronized、blocking IO、batch job。",
                    topics=[
                        "是否存在阻塞调用占满关键线程池",
                        "共享资源池是否被离线任务和在线流量争抢",
                        "锁粒度和临界区是否过大",
                        "热点路径是否频繁创建大对象或重复序列化",
                    ],
                    anti_patterns=[
                        "公共线程池既跑在线请求又跑大批量任务。",
                        "同步锁包住远端调用或数据库写。",
                        "每次请求都构造大型中间对象和字符串。",
                        "没有背压，异步队列无限堆积。",
                    ],
                    questions=[
                        "线程池满了以后，系统会怎么退化？",
                        "临界区内是否真的只保留最小必要操作？",
                        "批量任务是否应该错峰或隔离资源？",
                        "对象创建和序列化成本是否落在热点路径？",
                    ],
                    good_signal="资源隔离清晰，阻塞点可控，热点路径克制。",
                    bad_signal="把所有工作丢给公共池，靠运气不撞满。",
                    repair_pattern="隔离线程池、缩小锁范围、减少热点路径对象分配与阻塞 IO。",
                ),
                auto_dimension(
                    name="降级与恢复能力",
                    objective="确认链路在异常场景下可降级、可观测、可恢复。",
                    repo_focus="fallback、graceful degradation、health check、metrics、alerts。",
                    topics=[
                        "关键依赖失败时是否有明确降级路径",
                        "降级是否保持业务最小正确语义",
                        "系统是否暴露足够的指标和日志定位退化点",
                        "故障解除后链路能否自动恢复",
                    ],
                    anti_patterns=[
                        "依赖失败后直接 500，没有任何最小可用策略。",
                        "降级逻辑返回无上下文默认值，调用方无从知晓。",
                        "指标只看成功率，没有延迟、积压、超时信息。",
                        "熔断后永远打不开，需要人工重启。",
                    ],
                    questions=[
                        "失败时最小可用结果是什么？",
                        "调用方能否分辨正常结果和降级结果？",
                        "退化发生后有哪些指标会第一时间报警？",
                        "系统恢复后是否会自动回到正常模式？",
                    ],
                    good_signal="降级语义清晰、指标充分、恢复路径可预测。",
                    bad_signal="要么全挂，要么假装成功，运维完全看不见。",
                    repair_pattern="补降级语义、指标、告警和自动恢复控制。",
                ),
                auto_dimension(
                    name="热点链路与批处理影响",
                    objective="确认热点接口和批处理任务不会互相伤害。",
                    repo_focus="list/search APIs、batch/sync jobs、cache warmup、report tasks。",
                    topics=[
                        "热点链路是否被批处理共用资源拖慢",
                        "批量扫描和全量同步是否有节流与分页",
                        "缓存预热/失效是否会放大数据库或远端压力",
                        "在线流量与离线任务是否共享锁和表热点",
                    ],
                    anti_patterns=[
                        "凌晨批任务直接扫全表，白天接口一起受拖累。",
                        "缓存一失效就全流量打到数据库。",
                        "批量同步没有分页和断点恢复。",
                        "报表任务复用在线接口导致整体抖动。",
                    ],
                    questions=[
                        "离线任务和在线流量共享了哪些资源？",
                        "缓存失效时后端是否有保护？",
                        "批处理是否能分页、限速、断点继续？",
                        "热点表或热点服务是否会被多个任务同时冲击？",
                    ],
                    good_signal="热点链路有保护，批处理错峰、限速且可恢复。",
                    bad_signal="高峰期执行大任务，全系统一起抖。",
                    repair_pattern="隔离资源、分页限速、错峰执行并加强缓存保护。",
                ),
                auto_dimension(
                    name="稳定性可观测性",
                    objective="确认出问题时能迅速定位性能与可靠性根因。",
                    repo_focus="metrics、structured logs、trace、error budget、health endpoints。",
                    topics=[
                        "关键依赖和热点链路是否有延迟/错误/积压指标",
                        "日志是否保留超时、重试、降级、拒绝等关键上下文",
                        "trace 是否能串联跨服务性能瓶颈",
                        "是否能区分偶发异常、持续退化和资源饥饿",
                    ],
                    anti_patterns=[
                        "只有 error log，没有耗时和拒绝原因。",
                        "超时、重试、降级都没有埋点。",
                        "trace 串不起来，跨服务只能盲猜。",
                        "队列积压和线程池耗尽没有任何监控。",
                    ],
                    questions=[
                        "出问题时第一眼看哪个指标？",
                        "日志能否区分超时、熔断、限流和业务失败？",
                        "是否能从 trace 看到真正耗时段？",
                        "资源耗尽是否有前置信号而不是等到完全崩溃？",
                    ],
                    good_signal="指标、日志、trace 能共同定位退化点。",
                    bad_signal="系统慢了但没有任何能解释原因的数据。",
                    repair_pattern="补关键指标、结构化日志和 trace 标签，覆盖超时/重试/拒绝场景。",
                ),
            ],
            false_positives=[
                "不要把所有慢都归因于代码，先界定是否是数据库、缓存或外部依赖问题。",
                "不要代替 Redis、MQ、数据库专家做他们的专属判定。",
                "不要仅凭一个循环就说性能差，必须说明它落在什么负载路径上。",
                "不要把业务 fallback 语义错误误报成纯性能问题。",
            ],
            java_focus=[
                "检查 RestTemplate/WebClient/Feign 的 timeout、retry、bulkhead 配置。",
                "检查线程池、CompletableFuture、@Async、调度任务和阻塞点。",
                "检查 Resilience4j 或自研熔断/限流/降级逻辑。",
                "检查热点接口的缓存保护与批任务资源隔离。",
                "检查 Actuator、metrics、structured logging 和 tracing 标记。",
            ],
            scenarios=[
                {
                    "name": "写接口默认重试",
                    "change": "MR 给写远端接口加了通用 retry。",
                    "avoid": "不要只说“可能重复”，要看幂等和放大链路。",
                    "inspect": "搜重试配置、幂等保护、外部副作用和上游超时。",
                    "evidence": "说明失败时重试会如何放大请求或重复副作用。",
                    "bad_conclusion": "“重试越多越稳”。",
                    "repair": "建议仅对幂等读或显式幂等写启用重试，并设置上限与抖动。",
                },
                {
                    "name": "公共线程池跑批任务",
                    "change": "MR 把离线同步放进公共 executor。",
                    "avoid": "不要只说线程池共享不好，要指出在线链路会怎么被拖垮。",
                    "inspect": "查看 executor 使用方、峰值任务量和拒绝策略。",
                    "evidence": "指出资源争抢会落在哪些用户请求上。",
                    "bad_conclusion": "“先复用现有线程池最方便”。",
                    "repair": "建议隔离线程池或限速错峰执行。",
                },
                {
                    "name": "fallback 假装成功",
                    "change": "MR 新增默认值回退。",
                    "avoid": "不要只说 fallback 有风险，要看语义是否安全。",
                    "inspect": "查看调用方是否区分正常和降级结果。",
                    "evidence": "指出调用方会如何把降级结果误当真实结果。",
                    "bad_conclusion": "“有 fallback 就更高可用”。",
                    "repair": "建议返回可识别降级语义并补指标。",
                },
                {
                    "name": "缓存失效风暴",
                    "change": "MR 修改热点缓存键或失效策略。",
                    "avoid": "不要替 Redis 专家评价 key 设计，重点看后端压力放大。",
                    "inspect": "看缓存 miss 保护、批量加载和数据库回源路径。",
                    "evidence": "说明失效后会对哪些热点查询形成冲击。",
                    "bad_conclusion": "“最多就是慢一点”。",
                    "repair": "建议补 singleflight、回源限流或预热策略。",
                },
                {
                    "name": "阻塞调用进锁",
                    "change": "MR 在同步块内加入远端调用或数据库写。",
                    "avoid": "不要只说锁不好，要说临界区和等待放大。",
                    "inspect": "查看锁粒度、调用耗时和竞争方。",
                    "evidence": "指出会在哪些并发场景造成串行化或饥饿。",
                    "bad_conclusion": "“代码能工作就行”。",
                    "repair": "建议缩小锁范围，把阻塞调用移出临界区。",
                },
            ],
            final_checklist=[
                "是否明确说明了退化发生的负载或异常条件？",
                "是否指出了重试、超时、线程池、锁或降级中的具体风险点？",
                "是否避免越界评价数据库、Redis、MQ 细节？",
                "是否给出了可执行的保护措施和观测措施？",
            ],
            glossary=[
                "放大效应: 一个小故障被重试、堆积或竞争放大成系统性问题。",
                "背压: 上游在下游承载不足时主动减速或拒绝，避免雪崩。",
                "降级: 在依赖异常时保留最小可用能力的策略。",
                "可靠性: 系统在故障和负载变化下持续提供可接受服务的能力。",
            ],
        ),
        with_common_lists(
            expert_id="redis_analysis",
            title="Redis 分析专家代码检视规范",
            role="关注缓存、TTL、原子性、一致性和内存风险的 Redis 专家",
            mission="识别 key 设计、缓存一致性、过期策略和原子操作层面的 Redis 风险。",
            sources=[
                "[Redis Documentation](https://redis.io/docs/latest/)",
                "[Spring Data Redis Reference](https://docs.spring.io/spring-data/redis/reference/)",
                "[Redisson Reference Guide](https://redisson.pro/docs/)",
            ],
            in_scope=[
                "key 命名、key 维度、TTL 语义、预热和失效策略。",
                "缓存一致性、双写/删缓存时机、回源保护和击穿/雪崩/穿透。",
                "Lua、事务、setnx、分布式锁和原子操作语义。",
                "内存占用、热点 key、序列化格式和大 value 风险。",
            ],
            out_scope=[
                "不负责数据库 schema 和索引，这属于数据库专家。",
                "不负责业务语义正确性，这属于正确性专家。",
                "不负责 MQ 语义，这属于 MQ 专家。",
                "不负责总体性能策略，这属于性能专家。",
            ],
            handoff=[
                "看到数据库回源和查询计划问题时移交数据库专家。",
                "看到消息重试和幂等等消费语义时移交 MQ 专家。",
                "看到整体超时重试风暴时移交性能专家。",
                "看到业务字段含义和默认值错误时移交正确性专家。",
            ],
            repo_queries=[
                "搜索 cache、redis、ttl、expire、setIfAbsent、lua、lock、warmup。",
                "搜索 key 拼接点、序列化器、回源逻辑和缓存刷新时机。",
                "搜索删除缓存、更新缓存、双写和回放任务对缓存的影响。",
                "搜索热点接口、批量任务和定时任务是否共用同一 key 空间。",
            ],
            evidence=[
                "必须说明 key、TTL 或更新时机会怎样导致脏数据或击穿。",
                "原子性问题必须指出多步操作之间的竞态窗口。",
                "不要只说“缓存可能不一致”，要说明在哪种读写顺序下会错。",
                "内存和热点问题要指出 key 规模、value 体积或访问模式。",
            ],
            dimensions=[
                auto_dimension(
                    name="key 设计与 TTL",
                    objective="确认 key 维度和过期语义与业务场景匹配。",
                    repo_focus="key builder、prefix、tenant/user dimension、ttl config。",
                    topics=[
                        "key 是否包含必要业务维度和租户边界",
                        "TTL 是否与业务新鲜度和失效语义匹配",
                        "无 TTL 或 TTL 过长是否会造成长期脏数据",
                        "过短 TTL 是否会引发频繁回源和抖动",
                    ],
                    anti_patterns=[
                        "所有租户共用同一个 key 前缀而无边界。",
                        "关键缓存永不过期，但又没有明确刷新机制。",
                        "TTL 和业务有效期完全不匹配。",
                        "同一类数据不同地方用不同 key 规范。",
                    ],
                    questions=[
                        "这个 key 是否区分租户、用户、版本和场景？",
                        "TTL 到期后读者看到什么语义？",
                        "业务数据更新频率和 TTL 是否匹配？",
                        "这类 key 是否有统一命名与版本策略？",
                    ],
                    good_signal="key 维度完整、TTL 明确、命名稳定可维护。",
                    bad_signal="先缓存再说，key 和 TTL 都靠感觉。",
                    repair_pattern="统一 key 规范，按业务语义设置 TTL 并显式版本化。",
                ),
                auto_dimension(
                    name="缓存一致性",
                    objective="确认写数据库、删缓存、写缓存和回源顺序不会制造长时间脏数据。",
                    repo_focus="write-through、cache aside、delete cache、refresh、rebuild。",
                    topics=[
                        "写后删缓存还是写后更新缓存，顺序是否安全",
                        "并发读写时是否会回填旧值",
                        "异步更新缓存是否可能覆盖新值",
                        "回源失败时是否会留下脏缓存或空缓存",
                    ],
                    anti_patterns=[
                        "先删缓存再写库，窗口里读请求回填旧值。",
                        "异步重建把旧数据又写回缓存。",
                        "缓存 miss 后所有请求一起回源。",
                        "删除失败但系统无任何补偿或告警。",
                    ],
                    questions=[
                        "数据库写成功到缓存更新之间有多大窗口？",
                        "并发读写时会不会把旧值重新塞回缓存？",
                        "异步刷新是否带版本或时间比较？",
                        "删缓存失败后系统如何补偿？",
                    ],
                    good_signal="缓存更新时机清晰，并发场景下旧值不会轻易回填。",
                    bad_signal="缓存只是最佳努力，脏了多久没人知道。",
                    repair_pattern="明确 cache-aside 顺序，补版本比较、singleflight 和失败补偿。",
                ),
                auto_dimension(
                    name="原子性与锁",
                    objective="确认多步操作不会因竞态导致重复写、丢失更新或假锁。",
                    repo_focus="setnx、lua、multi/exec、redisson lock、unlock pattern。",
                    topics=[
                        "锁的获取、续期、释放是否成对且可追踪",
                        "多步写缓存是否需要 Lua 或事务保证原子性",
                        "setnx/expire 是否分步造成死锁窗口",
                        "锁失败、超时和重试是否会制造惊群",
                    ],
                    anti_patterns=[
                        "setnx 成功后进程崩溃，expire 还没设置。",
                        "unlock 不校验 owner，误删别人的锁。",
                        "多 key 更新分步执行，中间读者看到半成品。",
                        "锁失败后无等待策略，所有请求疯狂重试。",
                    ],
                    questions=[
                        "这个临界区真的需要锁，还是需要幂等？",
                        "锁的 owner 和 lease 是否可靠？",
                        "多步操作中间态是否可能被其他线程看到？",
                        "锁获取失败后系统如何退让？",
                    ],
                    good_signal="原子操作明确，锁语义可靠，失败策略可控。",
                    bad_signal="看起来有锁，实际上 owner、lease 和释放都不安全。",
                    repair_pattern="用 Lua/可靠锁封装原子操作，并明确 owner、lease 和失败退让。",
                ),
                auto_dimension(
                    name="热点与内存",
                    objective="确认 Redis 不会因热点 key、大 value 或无限增长而失控。",
                    repo_focus="large value、hash/list/set growth、scan、hot key、serialization size。",
                    topics=[
                        "是否存在高频访问单一 key",
                        "value 体积是否会导致网络和反序列化成本过大",
                        "集合类 key 是否无限增长",
                        "scan/遍历类操作是否落在在线链路",
                    ],
                    anti_patterns=[
                        "一个 key 挂整个列表或整个租户对象。",
                        "在线请求里 scan 全 key 空间。",
                        "集合 key 从不清理，越积越大。",
                        "热点 key 所有流量都命中单节点。",
                    ],
                    questions=[
                        "这个 key 的体积和访问频率有多大？",
                        "是否应该拆分大 value 或分页缓存？",
                        "集合是否有清理和裁剪策略？",
                        "热 key 是否需要本地缓存、分片或复制？",
                    ],
                    good_signal="key 体积可控，热点有保护，集合增长有上限。",
                    bad_signal="Redis 被当对象仓库使用，越用越胖。",
                    repair_pattern="拆 key、裁剪集合、规避在线 scan，并为热点做分流或保护。",
                ),
                auto_dimension(
                    name="序列化与兼容",
                    objective="确认缓存对象结构变化不会让旧缓存失效或读出错误语义。",
                    repo_focus="serializer、json schema、class name、version field、fallback parse。",
                    topics=[
                        "对象字段变化是否兼容旧缓存内容",
                        "序列化格式变化是否会让旧节点读失败",
                        "class/version 绑定是否导致发布期间不兼容",
                        "缓存回填和读取是否对未知字段足够稳健",
                    ],
                    anti_patterns=[
                        "类结构一变，旧缓存全无法反序列化。",
                        "序列化绑定完整类名，重构包名后全线失效。",
                        "读取失败后默默吞错并当空值处理。",
                        "多版本节点并行时缓存格式不兼容。",
                    ],
                    questions=[
                        "旧缓存对象在新代码里还能读吗？",
                        "序列化格式是否与类名强绑定？",
                        "读失败后系统会如何退化？",
                        "灰度发布期间多版本读写是否兼容？",
                    ],
                    good_signal="缓存格式有兼容策略，发布期间多版本可共存。",
                    bad_signal="只要代码类结构一改，缓存全失效或全报错。",
                    repair_pattern="引入版本字段、弱绑定序列化或安全回退读取策略。",
                ),
            ],
            false_positives=[
                "不要把所有数据不一致都归因于 Redis，先确认主源和更新顺序。",
                "不要代替性能专家评价整体吞吐，只看 Redis 自身语义和局部放大点。",
                "不要把缺缓存一律看成问题，先判断是否值得缓存。",
                "不要把锁问题误判成业务幂等等问题。",
            ],
            java_focus=[
                "检查 Spring Data Redis/Redisson 的 key、TTL、序列化和锁使用。",
                "检查 cache aside、删除缓存、预热和重建路径。",
                "检查 Lua、setnx、事务和 unlock owner 语义。",
                "检查热点 key、大 value、scan 操作和集合增长。",
                "检查灰度发布期缓存对象兼容性。",
            ],
            scenarios=[
                {
                    "name": "新增时间字段但缓存对象没跟上",
                    "change": "MR 修改 DTO/缓存对象字段结构。",
                    "avoid": "不要只看序列化成功，要看旧缓存兼容。",
                    "inspect": "看 serializer、缓存回填和旧值读取逻辑。",
                    "evidence": "指出旧缓存会读失败或读出错误默认值的位置。",
                    "bad_conclusion": "“缓存过期后自然就好了”。",
                    "repair": "建议补版本兼容或回退解析策略，并考虑灰度窗口。",
                },
                {
                    "name": "写后删缓存顺序不安全",
                    "change": "MR 调整写数据库和删缓存顺序。",
                    "avoid": "不要只说会脏，要说明并发窗口。",
                    "inspect": "查看读写并发路径和缓存回填逻辑。",
                    "evidence": "说明旧值如何被重新写回缓存。",
                    "bad_conclusion": "“删缓存总比更新缓存安全”。",
                    "repair": "建议显式设计 cache aside 顺序和并发保护。",
                },
                {
                    "name": "使用 setnx + expire 分步加锁",
                    "change": "MR 手写简单 Redis 锁。",
                    "avoid": "不要只说锁不好，要指出 owner/lease 风险。",
                    "inspect": "看加锁、续期、释放和异常路径。",
                    "evidence": "说明进程崩溃或超时后会留下什么问题。",
                    "bad_conclusion": "“能锁住就行”。",
                    "repair": "建议用 Lua 或成熟锁封装保证原子性和 owner 校验。",
                },
                {
                    "name": "缓存 miss 惊群",
                    "change": "MR 修改热点 key 或预热逻辑。",
                    "avoid": "不要代替数据库专家评价回源 SQL，只看 Redis 保护缺失。",
                    "inspect": "看 singleflight、锁、限流和预热策略。",
                    "evidence": "说明 miss 时会有多少请求同时回源。",
                    "bad_conclusion": "“命中率高就没问题”。",
                    "repair": "建议加单飞、回源限流或主动预热。",
                },
                {
                    "name": "大 value 挂在单 key",
                    "change": "MR 把更多字段直接塞进缓存对象。",
                    "avoid": "不要只说内存大，要看热点访问和序列化成本。",
                    "inspect": "看 value 大小、频率和读取模式。",
                    "evidence": "指出网络、反序列化或节点热点的具体影响。",
                    "bad_conclusion": "“Redis 内存够大就行”。",
                    "repair": "建议拆 key、分页缓存或按访问模式拆分对象。",
                },
            ],
            final_checklist=[
                "是否明确指出了 key、TTL、一致性或锁语义中的具体问题？",
                "是否给出了并发窗口、兼容窗口或热点证据？",
                "是否避免越界评价数据库和 MQ 语义？",
                "是否给出了 Redis 侧可执行的修复建议？",
            ],
            glossary=[
                "cache aside: 先读缓存，未命中回源，再回填缓存的常见模式。",
                "singleflight: 多个并发请求合并成一次回源。",
                "热 key: 短时间内访问量极高的单个 key。",
                "原子性: 一组操作要么全成功，要么对外不可见中间态。",
            ],
        ),
        with_common_lists(
            expert_id="mq_analysis",
            title="MQ 分析专家代码检视规范",
            role="关注消息投递、消费、幂等、顺序和堆积风险的 MQ 专家",
            mission="识别消息系统中的投递语义、消费幂等、重试死信和顺序性问题。",
            sources=[
                "[RabbitMQ Documentation](https://www.rabbitmq.com/docs)",
                "[Apache Kafka Documentation](https://kafka.apache.org/documentation/)",
                "[Spring AMQP Reference](https://docs.spring.io/spring-amqp/reference/)",
                "[Spring for Apache Kafka](https://docs.spring.io/spring-kafka/reference/)",
            ],
            in_scope=[
                "消息投递、确认、重试、死信、幂等和顺序语义。",
                "生产者和消费者的事务边界、发布时机和消息契约稳定性。",
                "积压、回放、重复消费、毒消息和补偿流程。",
                "分区/队列键设计是否支撑业务顺序和隔离需求。",
            ],
            out_scope=[
                "不负责 Redis 语义和缓存问题，这属于 Redis 专家。",
                "不负责数据库 schema 与索引，这属于数据库专家。",
                "不负责业务状态语义是否正确，这属于正确性专家。",
                "不负责总体吞吐策略，这属于性能专家。",
            ],
            handoff=[
                "看到 DB 事务和 outbox 细节时移交数据库专家。",
                "看到整体重试风暴和资源争抢时移交性能专家。",
                "看到消费后业务状态错误时移交正确性专家。",
                "看到安全鉴权和回调签名问题时移交安全专家。",
            ],
            repo_queries=[
                "搜索 producer、publisher、listener、consumer、ack、retry、dead letter、offset。",
                "搜索消息 key、routing key、partition key、group、queue 名。",
                "搜索 outbox、事务提交点和消费后的幂等记录。",
                "搜索失败重试、毒消息处理、回放工具和补偿流程。",
            ],
            evidence=[
                "必须说明消息在哪个阶段会重复、丢失、乱序或堆积。",
                "不能只看 listener 方法，要把生产者、broker 语义和消费者状态一起看。",
                "幂等问题要指出幂等键或去重记录哪里不足。",
                "顺序问题要指出分区/队列键与业务顺序需求的关系。",
            ],
            dimensions=[
                auto_dimension(
                    name="投递与发布时机",
                    objective="确认消息在正确的事务边界和业务时机发布。",
                    repo_focus="producer、outbox、transaction boundary、publish after commit。",
                    topics=[
                        "消息是否在数据库提交前过早发布",
                        "发布失败是否有补偿或重试策略",
                        "消息契约是否稳定且足以支持下游",
                        "生产者是否区分命令、事件和通知语义",
                    ],
                    anti_patterns=[
                        "数据库还没提交就直接发消息。",
                        "发消息失败直接吞掉，系统假装成功。",
                        "事件 payload 只是一堆内部字段快照，没有稳定语义。",
                        "同一业务动作既发事件又发通知但没有顺序约束。",
                    ],
                    questions=[
                        "如果数据库回滚，消息怎么办？",
                        "如果消息发送失败，调用方知道吗？",
                        "这个 payload 是否足以让消费者正确处理？",
                        "事件和通知的边界是否清楚？",
                    ],
                    good_signal="消息发布时机与业务提交一致，失败路径明确。",
                    bad_signal="消息只是顺手发一下，没有清晰边界和补偿。",
                    repair_pattern="对齐提交边界，必要时引入 outbox 或显式补偿。",
                ),
                auto_dimension(
                    name="消费幂等与重复处理",
                    objective="确认重复投递或重复消费不会造成多次副作用。",
                    repo_focus="consumer idempotency、dedup store、message key、status table。",
                    topics=[
                        "消费者是否有稳定幂等键",
                        "重试是否会重复写库、重复通知或重复记账",
                        "幂等记录是否具备正确作用域和生命周期",
                        "异常重试和人工回放是否共用同一幂等保护",
                    ],
                    anti_patterns=[
                        "以消息偏移量当幂等键，跨集群或重放完全失效。",
                        "只对正常重试做幂等，回放工具绕过保护。",
                        "幂等记录无 TTL 或无限增长。",
                        "业务主键不足以区分多次不同动作。",
                    ],
                    questions=[
                        "同一条消息被重复消费两次会怎样？",
                        "人工回放时是否仍能去重？",
                        "幂等键能否稳定标识一次业务动作？",
                        "幂等存储何时清理？",
                    ],
                    good_signal="重复投递、回放和重试都能被统一去重。",
                    bad_signal="正常链路也许没问题，一回放就乱套。",
                    repair_pattern="设计稳定幂等键，并让重试和回放共用同一保护机制。",
                ),
                auto_dimension(
                    name="顺序与分区语义",
                    objective="确认业务要求的顺序性被路由键和消费模型正确承载。",
                    repo_focus="partition key、routing key、consumer group、concurrency。",
                    topics=[
                        "业务要求顺序的实体是否总落到同一分区/队列",
                        "并发消费是否会打乱单实体顺序",
                        "重试和死信后是否丢失原始顺序假设",
                        "跨主题/跨队列协作是否错误依赖全局顺序",
                    ],
                    anti_patterns=[
                        "分区键随机，业务却要求按订单顺序处理。",
                        "listener 并发开很大，但没有按 key 串行。",
                        "死信重投后顺序完全乱掉。",
                        "多个 topic 之间默认假设先后顺序。",
                    ],
                    questions=[
                        "真正要求顺序的是哪个业务实体？",
                        "分区键是否与该实体一致？",
                        "重试和死信回来后顺序还能保证吗？",
                        "是否其实应该设计成无顺序依赖？",
                    ],
                    good_signal="顺序需求只在必要维度上保证，路由键与业务实体对齐。",
                    bad_signal="想要顺序，却没有任何路由或串行策略。",
                    repair_pattern="按业务实体设计分区键或去顺序化处理，避免错误全局顺序假设。",
                ),
                auto_dimension(
                    name="失败、重试与死信",
                    objective="确认失败消息不会无限重试、堆积或静默丢失。",
                    repo_focus="retry topic、DLQ、backoff、poison message、manual replay。",
                    topics=[
                        "失败重试是否有上限和退避",
                        "毒消息是否进入死信并可排查处理",
                        "重试后是否仍保留上下文与错误原因",
                        "人工回放工具是否安全且可审计",
                    ],
                    anti_patterns=[
                        "消费失败就立即无限重试。",
                        "死信队列存在但没人监控也没人处理。",
                        "重试消息丢失原始上下文和业务键。",
                        "人工回放随便重放，没有审计和幂等保护。",
                    ],
                    questions=[
                        "这条消息最坏会重试多少次？",
                        "毒消息进入哪里，谁会处理？",
                        "重试间隔和退避是否合理？",
                        "人工回放是否有权限和审计控制？",
                    ],
                    good_signal="重试有边界，死信可观测、可处理、可回放。",
                    bad_signal="失败后不是无限打就是直接消失。",
                    repair_pattern="补退避、死信监控、上下文保留和安全回放策略。",
                ),
                auto_dimension(
                    name="积压与回放链路",
                    objective="确认消息积压、补偿和重放不会把系统拖垮或造成二次错误。",
                    repo_focus="lag metrics、replay job、catch-up strategy、batch consumer。",
                    topics=[
                        "积压出现时是否有观测和限速策略",
                        "重放是否会与在线消费互相干扰",
                        "补偿消费是否和主消费共享同一副作用路径",
                        "高峰期回放是否会压垮数据库和下游",
                    ],
                    anti_patterns=[
                        "积压很大时直接全速追平，拖垮下游。",
                        "回放和在线消费共享同一消费组或资源池。",
                        "补偿逻辑走了和主链不同的副作用路径。",
                        "积压指标没人看，直到严重延迟才发现。",
                    ],
                    questions=[
                        "积压多少算异常，谁来感知？",
                        "回放是否需要独立资源和限速？",
                        "补偿是否仍然满足幂等和顺序要求？",
                        "追平积压时下游能承受吗？",
                    ],
                    good_signal="积压可观测、回放受控、补偿与主链语义一致。",
                    bad_signal="积压只是数字，没人知道怎么处理，回放靠运气。",
                    repair_pattern="补 lag 监控、回放限速和隔离资源，并统一补偿语义。",
                ),
            ],
            false_positives=[
                "不要把业务状态错误直接归咎于 MQ，要区分消息语义和业务处理错误。",
                "不要替数据库专家判断 outbox 表结构或索引细节。",
                "不要把所有重试都当错，重点看幂等和放大风险。",
                "不要把积压简单视为性能问题，要看投递语义和回放策略。",
            ],
            java_focus=[
                "检查 Spring Kafka/Spring AMQP 的 ack、retry、error handler 和 DLQ 配置。",
                "检查 producer 发送时机、事务边界和 outbox。",
                "检查 listener 幂等键、分区键和并发消费设置。",
                "检查回放工具、补偿作业和 lag 指标。",
                "检查 dead letter、retry topic 和 poison message 处理。",
            ],
            scenarios=[
                {
                    "name": "数据库提交前发事件",
                    "change": "MR 在事务方法中直接发送消息。",
                    "avoid": "不要只说 outbox 更好，要指出一致性破口。",
                    "inspect": "查看事务边界、发布时机和失败路径。",
                    "evidence": "说明数据库回滚时消费者会看到什么假事实。",
                    "bad_conclusion": "“先发后写问题不大”。",
                    "repair": "建议对齐提交边界，必要时改用 outbox。",
                },
                {
                    "name": "消费者幂等键不足",
                    "change": "MR 新增消费逻辑但沿用旧去重字段。",
                    "avoid": "不要只说可能重复，要指出哪类业务动作无法区分。",
                    "inspect": "查看 message key、业务主键和去重存储。",
                    "evidence": "给出两个会被错误视为同一次消费的样例。",
                    "bad_conclusion": "“有 messageId 就够了”。",
                    "repair": "建议设计稳定的业务幂等键并覆盖回放场景。",
                },
                {
                    "name": "顺序实体与分区键不匹配",
                    "change": "MR 新增并发消费或改动 partition key。",
                    "avoid": "不要只说会乱序，要指出哪个实体受影响。",
                    "inspect": "看业务实体、路由键和消费者并发度。",
                    "evidence": "说明同一实体消息如何被不同分区或线程并行处理。",
                    "bad_conclusion": "“Kafka/RabbitMQ 自己会保证顺序”。",
                    "repair": "建议按实体维度设计分区键，必要时串行处理。",
                },
                {
                    "name": "无限重试毒消息",
                    "change": "MR 自定义异常处理但无 DLQ。",
                    "avoid": "不要只说缺 DLQ，要看失败边界和上下文保留。",
                    "inspect": "查看 retry 次数、退避、死信和日志。",
                    "evidence": "指出这条毒消息会如何一直卡住消费。",
                    "bad_conclusion": "“重试总能成功”。",
                    "repair": "建议设置退避、重试上限和死信处理路径。",
                },
                {
                    "name": "人工回放无幂等",
                    "change": "MR 新增 replay 工具或补偿任务。",
                    "avoid": "不要只从运维方便角度看，要看副作用和审计。",
                    "inspect": "查看权限、审计、幂等和资源隔离。",
                    "evidence": "指出重复回放会怎样影响系统。",
                    "bad_conclusion": "“需要的时候人工注意点就行”。",
                    "repair": "建议给回放加权限、审计和统一幂等保护。",
                },
            ],
            final_checklist=[
                "是否明确指出了消息在哪个阶段会出问题？",
                "是否给出了幂等、顺序、重试或死信的具体证据？",
                "是否避免越界评价数据库和业务语义？",
                "是否给出了可落地的消息链修复方案？",
            ],
            glossary=[
                "幂等消费: 同一消息或同一业务动作重复到达也只生效一次。",
                "死信: 超出重试或不可处理的消息进入的隔离通道。",
                "顺序语义: 某个业务实体要求消息按产生顺序被处理。",
                "毒消息: 重试多次仍无法成功处理且会阻塞或污染主链路的消息。",
            ],
        ),
        with_common_lists(
            expert_id="test_verification",
            title="测试与验证专家代码检视规范",
            role="关注测试分层、回归保护、断言质量和可验证性的测试专家",
            mission="识别缺失测试、弱断言、错误夹具和无法保护回归的验证缺口。",
            sources=[
                "[JUnit 5 User Guide](https://junit.org/junit5/docs/current/user-guide/)",
                "[Spring Boot Testing Reference](https://docs.spring.io/spring-boot/docs/current/reference/html/features.html#features.testing)",
                "[Google Testing Blog](https://testing.googleblog.com/)",
            ],
            in_scope=[
                "单测、集成测试、契约测试、回归测试和夹具设计。",
                "断言粒度、覆盖关键风险、异常路径与回归保护能力。",
                "可测性前提，例如依赖注入、时间控制、数据构造和可观测输出。",
                "测试是否真正覆盖这次 MR 改动的关键风险点。",
            ],
            out_scope=[
                "不直接定义业务语义是否正确，这属于正确性专家。",
                "不负责架构边界，这属于架构专家。",
                "不负责安全漏洞定性，这属于安全专家。",
                "不负责数据库具体索引设计，这属于数据库专家。",
            ],
            handoff=[
                "看到业务语义偏差时移交正确性专家。",
                "看到可测性问题来自架构边界失真时移交架构或可维护性专家。",
                "看到安全风险缺少安全测试时可附带建议并移交安全专家。",
                "看到数据库迁移缺少验证时移交数据库专家给出主结论。",
            ],
            repo_queries=[
                "搜索与改动文件同名或同模块的 test/spec/integration 测试。",
                "搜索异常路径、回归场景、时间/幂等/兼容性相关测试。",
                "搜索夹具、builders、test data factory 和 mock/stub 使用方式。",
                "搜索是否已有类似历史 bug 的回归测试模板可复用。",
            ],
            evidence=[
                "测试问题必须说明缺哪类保护，以及为什么这次改动需要它。",
                "不能把所有没改测试的 MR 都判成问题，必须结合风险级别。",
                "弱断言问题要指出测试虽存在但无法捕获此次回归。",
                "可测性问题要说明为什么后续无法有效补测。",
            ],
            dimensions=[
                auto_dimension(
                    name="风险覆盖",
                    objective="确认这次改动的关键风险点有对应测试保护。",
                    repo_focus="unit/integration/contract/regression tests 对应改动链路。",
                    topics=[
                        "新增字段、状态、异常路径是否有对应测试",
                        "跨文件语义变更是否有端到端或集成保护",
                        "兼容性、旧数据、回放场景是否有回归测试",
                        "异步、重试、幂等等高风险路径是否有验证",
                    ],
                    anti_patterns=[
                        "主逻辑改了，测试一行没动且无其他保护。",
                        "只测 happy path，不测失败和边界路径。",
                        "新增兼容逻辑但没有旧数据/旧消息样本。",
                        "异步链路只测 producer 不测 consumer 结果。",
                    ],
                    questions=[
                        "这次改动最可能回归的点是什么，是否有测试守住？",
                        "如果只有一条测试，该测哪条风险？",
                        "是否需要集成测试而不是单测？",
                        "旧数据/旧消息/旧缓存场景是否被覆盖？",
                    ],
                    good_signal="测试直接覆盖本次改动高风险点，能在回归时及时报警。",
                    bad_signal="测试存在很多，但没有一条真正守住这次风险。",
                    repair_pattern="新增最小有力测试，优先覆盖高风险变更和历史易错点。",
                ),
                auto_dimension(
                    name="断言质量",
                    objective="确认现有测试不是形式存在，而是真能发现错误。",
                    repo_focus="assertion、mock verification、snapshot、exception assertion。",
                    topics=[
                        "断言是否覆盖关键输出和副作用",
                        "是否只断言调用发生而不验证结果语义",
                        "snapshot/字符串断言是否过于脆弱或过于宽泛",
                        "异常测试是否断言了错误类型和关键信息",
                    ],
                    anti_patterns=[
                        "测试只 assertNotNull，根本测不出字段错。",
                        "全靠 mock verify 调用次数，不检查结果。",
                        "快照太大，变什么都看不出来。",
                        "异常测试只 catch Exception 就算通过。",
                    ],
                    questions=[
                        "如果当前 bug 出现，这条测试真的会失败吗？",
                        "断言是在验证业务结果还是验证实现细节？",
                        "是否需要补更窄、更有语义的断言？",
                        "是否过度依赖 mock 导致真实问题被掩盖？",
                    ],
                    good_signal="断言能准确抓住业务结果、关键副作用和异常边界。",
                    bad_signal="测试绿了但线上仍可轻松回归同类问题。",
                    repair_pattern="改成语义化断言，减少空泛断言和纯交互式断言。",
                ),
                auto_dimension(
                    name="测试分层与夹具",
                    objective="确认测试层级合理，夹具和 mock 不会扭曲真实行为。",
                    repo_focus="unit/integration slice、builders、fixtures、test containers、mock scope。",
                    topics=[
                        "该风险更适合单测、集成测还是契约测试",
                        "夹具是否隐藏了真实默认值和边界条件",
                        "mock 是否把关键行为全部替掉，导致测试失真",
                        "测试数据工厂是否让场景表达清晰可维护",
                    ],
                    anti_patterns=[
                        "把所有依赖都 mock 掉，根本看不到真实集成问题。",
                        "测试数据工厂默认值太魔法，没人知道场景是什么。",
                        "同一个集成测试承担太多意图，失败难定位。",
                        "为了快，把真正关键的转换层全绕过。",
                    ],
                    questions=[
                        "这个风险需要哪一层测试才能发现？",
                        "当前夹具是否掩盖了真实边界值？",
                        "mock 掉这个依赖后，还能测出此次风险吗？",
                        "测试失败时能否快速定位原因？",
                    ],
                    good_signal="测试分层清晰，夹具表达意图，mock 只替换必要边界。",
                    bad_signal="测试跑很快，但测到的是理想化世界。",
                    repair_pattern="选择正确测试层级，收敛 mock 并让夹具显式表达场景。",
                ),
                auto_dimension(
                    name="可测性前提",
                    objective="确认代码结构允许后续补测试，而不是把验证成本推高到不可接受。",
                    repo_focus="static dependency、time/random/env access、framework callback-heavy code。",
                    topics=[
                        "核心逻辑是否可直接调用",
                        "时间、随机性、环境依赖是否可控",
                        "副作用是否可在测试中观察到",
                        "构造测试输入是否需要大量无关步骤",
                    ],
                    anti_patterns=[
                        "逻辑散在注解回调和 static 工具类里。",
                        "系统时钟、随机数和线程上下文直接读写。",
                        "副作用没有任何可观察输出或可替换接口。",
                        "一个简单测试要准备一屏幕对象。",
                    ],
                    questions=[
                        "如果现在补一条回归测试，最难的点是什么？",
                        "能否把时间、随机性、IO 提升成可注入依赖？",
                        "是否需要先做小重构再补测试？",
                        "测试数据是否过于依赖隐式默认值？",
                    ],
                    good_signal="补测试的成本合理，关键逻辑可独立验证。",
                    bad_signal="每条测试都像搭脚手架，最终没人愿意补。",
                    repair_pattern="先做小步可测性重构，再补高价值回归测试。",
                ),
                auto_dimension(
                    name="回归与发布验证",
                    objective="确认测试之外仍有必要的发布前验证与回归策略。",
                    repo_focus="smoke、migration validation、replay、contract check、manual verification notes。",
                    topics=[
                        "本次改动是否需要额外 smoke 或灰度验证",
                        "迁移、回放、缓存、消息等是否需要专门验证步骤",
                        "人工验证步骤是否清晰可复现",
                        "是否有值得沉淀成回归测试的历史问题模式",
                    ],
                    anti_patterns=[
                        "全靠人工点点看，但没有任何操作步骤。",
                        "发布验证只看服务能启动，不看关键链路。",
                        "历史上踩过的坑这次又没守住。",
                        "需要人工验证的地方没有记录如何验证。",
                    ],
                    questions=[
                        "上线前至少该跑哪条 smoke？",
                        "哪些验证很适合下一轮沉淀成自动测试？",
                        "这次是否涉及 migration、回放或异步链路验证？",
                        "人工验证步骤是否足够具体？",
                    ],
                    good_signal="自动测试与必要的人工验证形成闭环。",
                    bad_signal="发布靠感觉，不靠验证方案。",
                    repair_pattern="补最小 smoke、记录人工验证步骤，并把高频问题沉淀成回归测试。",
                ),
            ],
            false_positives=[
                "不要把所有未改测试的改动都判成问题，先看风险和已有保护。",
                "不要把覆盖率数字当充分证据，关键是是否守住这次改动的风险。",
                "不要代替正确性专家定义业务结论，只评价验证是否足够。",
                "不要因为使用 mock 就一概否定，重点看 mock 是否遮蔽关键风险。",
            ],
            java_focus=[
                "检查 JUnit 5、Spring Boot Test、Testcontainers、MockMvc/WebTestClient 等测试层级选择。",
                "检查断言是否覆盖字段语义、异常边界和副作用。",
                "检查 builder/fixture/factory 是否表达真实场景。",
                "检查时间、随机性、环境和外部依赖是否可控。",
                "检查 migration、消息、缓存和回放是否有发布前验证。",
            ],
            scenarios=[
                {
                    "name": "DTO 新增字段但测试只断言对象非空",
                    "change": "MR 新增输出字段，测试没有改或只做弱断言。",
                    "avoid": "不要只说缺测试，要指出当前断言守不住什么。",
                    "inspect": "看断言内容、transformer 测试和接口测试。",
                    "evidence": "说明字段为空或错误时测试依然会绿。",
                    "bad_conclusion": "“有测试就够了”。",
                    "repair": "建议补字段级断言或契约测试。",
                },
                {
                    "name": "异常路径未保护",
                    "change": "MR 新增 fallback、重试或异常分支。",
                    "avoid": "不要只补 happy path 测试。",
                    "inspect": "看异常类型、重试次数和降级结果。",
                    "evidence": "指出失败路径当前完全无测试覆盖。",
                    "bad_conclusion": "“异常很少发生，不用测”。",
                    "repair": "建议补失败分支和边界条件测试。",
                },
                {
                    "name": "迁移需要验证但没有 smoke",
                    "change": "MR 改了 schema 或消息/缓存契约。",
                    "avoid": "不要只停留在单测，关注发布验证。",
                    "inspect": "看 migration、旧数据、回放和灰度步骤。",
                    "evidence": "指出自动测试之外仍有关键风险未覆盖。",
                    "bad_conclusion": "“单测都过了，可以直接上”。",
                    "repair": "建议补 smoke、迁移验证或回放验证步骤。",
                },
                {
                    "name": "mock 遮蔽真实问题",
                    "change": "MR 相关测试把关键转换层或仓储行为全 mock 掉。",
                    "avoid": "不要一概反 mock，要指出当前风险被遮蔽了。",
                    "inspect": "看 mock 范围、真实依赖替换和断言对象。",
                    "evidence": "说明真实集成问题在当前测试中完全不可能暴露。",
                    "bad_conclusion": "“测试很多，所以有保障”。",
                    "repair": "建议至少补一条更接近真实链路的集成测试。",
                },
                {
                    "name": "回归 bug 没沉淀",
                    "change": "MR 修复了已知线上问题但没补回归测试。",
                    "avoid": "不要只要求补测试，要说明这是高价值回归保护。",
                    "inspect": "看 bug 触发条件、最小复现场景和现有测试模板。",
                    "evidence": "指出同类问题未来仍可轻易复发。",
                    "bad_conclusion": "“修好了就行，不必留测试”。",
                    "repair": "建议把最小复现场景沉淀成回归用例。",
                },
            ],
            final_checklist=[
                "是否明确指出了缺少哪类验证以及它守不住哪类风险？",
                "是否避免把覆盖率或测试数量当成唯一标准？",
                "是否给出了可执行的测试或 smoke 补充建议？",
                "是否兼顾了自动测试与发布前验证？",
            ],
            glossary=[
                "回归保护: 为防止同类问题再次出现而建立的测试或验证机制。",
                "弱断言: 即使结果错误，测试也大概率不会失败的断言。",
                "测试夹具: 用来快速构造场景的数据、对象或环境准备代码。",
                "可测性: 代码被可靠验证所需要付出的结构成本。",
            ],
        ),
        with_common_lists(
            expert_id="frontend_accessibility",
            title="前端可访问性专家代码检视规范",
            role="关注语义结构、键盘可访问性、表单反馈和无障碍体验的前端专家",
            mission="识别语义标签、焦点管理、键盘交互、表单可用性与视觉反馈中的可访问性问题。",
            sources=[
                "[WCAG 2.2](https://www.w3.org/TR/WCAG22/)",
                "[WAI-ARIA Authoring Practices](https://www.w3.org/WAI/ARIA/apg/)",
                "[MDN Accessibility](https://developer.mozilla.org/en-US/docs/Web/Accessibility)",
            ],
            in_scope=[
                "语义 HTML、ARIA 使用、键盘导航、焦点顺序和屏幕阅读器可理解性。",
                "表单标签、错误反馈、状态提示、色彩对比和可点击目标。",
                "动态内容更新、对话框、菜单、标签页、折叠区等交互组件。",
                "前端文案、状态反馈和视觉提示是否对所有用户可用。",
            ],
            out_scope=[
                "不负责后端业务语义和数据库设计。",
                "不负责服务端安全与缓存、消息等后端问题。",
                "不负责整体架构分层设计。",
            ],
            handoff=[
                "看到纯前端性能瓶颈时可提醒但移交性能专家。",
                "看到业务状态语义错误时移交正确性专家。",
                "看到后端接口权限与数据暴露问题时移交安全专家。",
                "看到测试缺失时移交测试专家。",
            ],
            repo_queries=[
                "搜索 JSX/HTML 结构、role、aria- 属性、tabIndex、focus 管理。",
                "搜索 Form、Modal、Drawer、Tabs、Collapse、Table 等组件用法。",
                "搜索错误提示、加载态、空态、禁用态和状态文案。",
                "搜索键盘事件、快捷键、焦点陷阱和动态渲染内容。",
            ],
            evidence=[
                "必须指出具体交互、具体元素和具体用户群会受什么影响。",
                "不要只说“不够无障碍”，要说明键盘、读屏或低视力用户哪里会卡住。",
                "纯视觉审美差异不能算问题，必须映射到可访问性规则。",
                "如果只是建议优化而非违反规则，应降级为 design_concern。",
            ],
            dimensions=[
                auto_dimension(
                    name="语义结构",
                    objective="确认页面结构、标题层级和交互元素具备正确语义。",
                    repo_focus="heading、button/link、list/table、landmark、aria labels。",
                    topics=[
                        "交互元素是否使用正确语义标签而不是 div/span 假装按钮",
                        "标题层级和区域 landmark 是否有助于读屏理解页面结构",
                        "表格、列表、表单是否使用合适语义容器",
                        "图标按钮、无文本控件是否有可读标签",
                    ],
                    anti_patterns=[
                        "div 绑定 onClick 却无 role 和键盘支持。",
                        "页面大标题和子标题层级乱跳。",
                        "纯图标按钮没有 aria-label。",
                        "表格被当成纯布局容器使用。",
                    ],
                    questions=[
                        "读屏用户能否理解这个控件是什么？",
                        "标题和区域结构是否反映真实信息层级？",
                        "这个元素真的是按钮、链接还是静态文本？",
                        "无文本控件是否有可读名字？",
                    ],
                    good_signal="交互元素语义准确、页面结构可被辅助技术理解。",
                    bad_signal="纯靠视觉摆放，语义层一片空白。",
                    repair_pattern="改用正确语义元素并补可读标签。",
                ),
                auto_dimension(
                    name="键盘与焦点",
                    objective="确认不使用鼠标也能完成核心操作，焦点顺序可预测。",
                    repo_focus="tab order、focus trap、keyboard handlers、modal/drawer/menu/tab。",
                    topics=[
                        "所有核心交互是否可通过键盘完成",
                        "弹窗、抽屉和菜单是否正确管理焦点",
                        "展开/切换/选择控件是否遵循常见键盘模式",
                        "焦点可见性是否足够清晰",
                    ],
                    anti_patterns=[
                        "只能点击不能 Enter/Space 触发。",
                        "打开弹窗后焦点还留在背景页面。",
                        "关闭弹窗后焦点丢失或回不到触发点。",
                        "自定义 Tab/Collapse 组件键盘行为与用户预期不符。",
                    ],
                    questions=[
                        "只用键盘能否完整走通主路径？",
                        "弹窗关闭后焦点去哪？",
                        "当前组件是否遵循 APG 里的键盘模式？",
                        "焦点轮廓是否被隐藏了？",
                    ],
                    good_signal="键盘主路径完整，焦点移动可预期且可见。",
                    bad_signal="鼠标用户没问题，键盘用户直接卡住。",
                    repair_pattern="补原生键盘支持、焦点管理和清晰 focus style。",
                ),
                auto_dimension(
                    name="表单与反馈",
                    objective="确认输入、校验、错误和状态反馈对所有用户都清晰可达。",
                    repo_focus="label、help、error message、loading、disabled、success state。",
                    topics=[
                        "输入框是否有清晰标签而不只靠 placeholder",
                        "错误提示是否与具体字段绑定且能被读屏感知",
                        "加载态、禁用态、成功态是否有明确反馈",
                        "表单校验是否在视觉和语义上都可理解",
                    ],
                    anti_patterns=[
                        "placeholder 当 label 使用。",
                        "错误提示只变红，不给文本说明。",
                        "按钮 disabled 了但没有原因提示。",
                        "提交成功只靠页面细微变化，缺少状态反馈。",
                    ],
                    questions=[
                        "用户是否知道这个输入要填什么？",
                        "错误发生时，读屏和视觉用户是否都能知道？",
                        "加载中和成功后，系统给了什么明确信号？",
                        "禁用态是否说明了为什么不可操作？",
                    ],
                    good_signal="表单标签、校验和反馈完整且可被多种用户理解。",
                    bad_signal="只有颜色在变化，用户并不知道发生了什么。",
                    repair_pattern="补标签、错误文本、状态区域和可感知反馈。",
                ),
                auto_dimension(
                    name="视觉可达性",
                    objective="确认颜色、对比、尺寸和可点击区域不会阻碍使用。",
                    repo_focus="contrast、font size、hit area、spacing、status color usage。",
                    topics=[
                        "文本与背景对比是否足够",
                        "状态是否不仅靠颜色传达",
                        "按钮和控件点击区域是否足够大",
                        "缩放或窄屏下信息是否仍可阅读和操作",
                    ],
                    anti_patterns=[
                        "浅灰文案放在浅色背景上几乎看不见。",
                        "成功/失败只靠红绿区分。",
                        "小图标点位极小，触控很难命中。",
                        "放大后布局断裂导致内容遮挡。",
                    ],
                    questions=[
                        "低视力或色弱用户能否区分状态？",
                        "触屏用户能否稳定点中关键操作？",
                        "页面缩放后是否仍可阅读？",
                        "提示信息是否有文字和图形双重表达？",
                    ],
                    good_signal="视觉信息兼顾对比、尺寸和非颜色提示。",
                    bad_signal="只有正常视力和鼠标用户体验良好。",
                    repair_pattern="提高对比度、扩大点击区域并增加文字/图标双重提示。",
                ),
                auto_dimension(
                    name="动态内容与组件模式",
                    objective="确认动态更新内容和复合组件遵循可访问性模式。",
                    repo_focus="live region、modal、tabs、accordion、dropdown、table updates。",
                    topics=[
                        "动态加载内容是否有适当提示",
                        "标签页、折叠区、菜单是否遵循 ARIA 模式",
                        "数据更新是否让读屏用户知道上下文变化",
                        "复杂表格和列表是否保留可理解导航",
                    ],
                    anti_patterns=[
                        "异步加载完成后页面变化无任何提示。",
                        "自定义 tabs 只改样式，不维护 aria-selected 等状态。",
                        "折叠区展开收起不更新语义状态。",
                        "表格更新后用户上下文完全丢失。",
                    ],
                    questions=[
                        "动态更新后，用户是否知道发生了什么？",
                        "组件状态是否同步反映到 ARIA 属性？",
                        "切换 tabs/accordion 后上下文是否连续？",
                        "表格变化是否让读屏用户还能定位当前项？",
                    ],
                    good_signal="动态内容变化可感知，复合组件遵循已知模式。",
                    bad_signal="视觉上能用，辅助技术用户完全失去上下文。",
                    repair_pattern="补 live region、ARIA 状态和标准组件交互模式。",
                ),
            ],
            false_positives=[
                "不要把纯设计偏好当作无障碍问题，必须能映射到真实使用障碍。",
                "不要因为使用组件库就默认无障碍已解决，仍要看实际用法。",
                "不要替后端专家评论接口问题，只看前端呈现和交互可达性。",
                "不要把所有视觉瑕疵都视作 WCAG 违规。",
            ],
            java_focus=[
                "前端专家保留前端规范，不套用 Java 后端规则。",
                "重点检查 React/Ant Design 组件的语义使用、状态反馈和交互模式。",
                "检查表格、折叠区、弹窗、标签页和上传组件的无障碍实现。",
                "检查文本提示、错误反馈和焦点管理。",
                "检查动态加载内容和长列表的可达性。",
            ],
            scenarios=[
                {
                    "name": "自定义按钮用 div 实现",
                    "change": "MR 新增可点击卡片或图标区。",
                    "avoid": "不要只说语义差，要指出键盘和读屏会怎么受阻。",
                    "inspect": "看 role、tabIndex、键盘事件和可读标签。",
                    "evidence": "说明非鼠标用户无法触发或无法理解它。",
                    "bad_conclusion": "“样式好看就行”。",
                    "repair": "建议改为 button/link 或补完整语义与键盘支持。",
                },
                {
                    "name": "表单错误只靠红色",
                    "change": "MR 调整表单样式但隐藏了文本提示。",
                    "avoid": "不要只谈颜色，要看错误是否可感知。",
                    "inspect": "看 label、help、error 文本和 aria 属性。",
                    "evidence": "指出读屏用户和色弱用户都无法得知问题。",
                    "bad_conclusion": "“界面已经变红，用户会知道”。",
                    "repair": "建议补文本错误提示和语义关联。",
                },
                {
                    "name": "弹窗焦点丢失",
                    "change": "MR 新增 modal/drawer 工作流。",
                    "avoid": "不要只看能不能打开，要看焦点生命周期。",
                    "inspect": "看打开时初始焦点、关闭后焦点返回和 esc/tab 行为。",
                    "evidence": "说明键盘用户在哪一步迷失。",
                    "bad_conclusion": "“能点关闭就没问题”。",
                    "repair": "建议补焦点陷阱和关闭后回焦。",
                },
                {
                    "name": "折叠内容默认全展开",
                    "change": "MR 在知识库或专家中心展示大量文档内容。",
                    "avoid": "不要只说页面乱，要看可访问性和可读性负担。",
                    "inspect": "看标题、折叠按钮、aria-expanded 和内容层级。",
                    "evidence": "指出用户难以快速定位、读屏冗长的问题。",
                    "bad_conclusion": "“多展示一点内容更直观”。",
                    "repair": "建议默认折叠并提供明确的展开控制和状态说明。",
                },
                {
                    "name": "状态只靠 badge 颜色",
                    "change": "MR 新增状态标签或风险级别显示。",
                    "avoid": "不要只盯颜色搭配，要看信息是否能脱离颜色理解。",
                    "inspect": "看文本、图标、对比度和语义提示。",
                    "evidence": "说明色弱用户无法区分状态。",
                    "bad_conclusion": "“颜色已经足够明显”。",
                    "repair": "建议增加文字、图标和对比更强的视觉表达。",
                },
            ],
            final_checklist=[
                "是否明确说明了哪类用户、哪一步交互会受影响？",
                "是否给出了语义、键盘、反馈或视觉层面的具体证据？",
                "是否避免把纯审美问题误报成无障碍问题？",
                "是否给出了前端可直接落地的修复建议？",
            ],
            glossary=[
                "可访问性: 不同能力与设备条件下用户都能感知、理解并操作界面。",
                "焦点管理: 键盘焦点在界面中的位置、顺序和可见性控制。",
                "live region: 向辅助技术宣布动态更新内容的语义区域。",
                "语义结构: 让页面结构和控件意图可被机器与人同时理解的标记方式。",
            ],
        ),
    ]
)

PROFILES.extend(
    [
        with_common_lists(
            expert_id="maintainability_code_health",
            title="可维护性与代码健康专家代码检视规范",
            role="关注复杂度、重复、可读性和演进成本的代码健康审视专家",
            mission="识别让代码难读、难改、难测、难复用的结构性可维护性问题。",
            sources=[
                "[Google Java Style Guide](https://google.github.io/styleguide/javaguide.html)",
                "[Refactoring by Martin Fowler](https://martinfowler.com/books/refactoring.html)",
                "[Clean Code Summary](https://blog.cleancoder.com/)",
            ],
            in_scope=[
                "方法和类复杂度、重复逻辑、命名、职责散布、局部可读性。",
                "可演进性、局部抽象质量、边界内重复实现和坏味道累积。",
                "可测试性前提，例如隐藏副作用、难注入依赖和难构造输入。",
                "异常处理、日志结构、配置读取方式对可维护性的影响。",
            ],
            out_scope=[
                "不负责业务语义是否正确，这属于正确性专家。",
                "不负责架构边界和上下文设计，这属于架构或 DDD 专家。",
                "不负责安全定性，这属于安全专家。",
                "不负责性能瓶颈归因，这属于性能专家。",
            ],
            handoff=[
                "看到分层越界或模块耦合时移交架构专家。",
                "看到业务状态或字段语义错误时移交正确性专家。",
                "看到测试覆盖缺口时移交测试专家。",
                "看到数据库、Redis、MQ 细节时移交对应基础设施专家。",
            ],
            repo_queries=[
                "搜索同名逻辑是否在多个 service/handler/util 中重复。",
                "搜索长方法、深层嵌套、布尔参数和分支复制。",
                "搜索异常处理、日志模板、配置读取和常量散落位置。",
                "搜索单测构造是否需要大量样板或很难注入依赖。",
                "搜索 util/helper/static 入口是否演化成隐藏耦合点。",
            ],
            evidence=[
                "必须指出维护成本为什么会升高，而不是只说代码不好看。",
                "重复逻辑必须至少指出两处或更多落点。",
                "复杂度问题应给出嵌套、分支、依赖或副作用层面的具体证据。",
                "能用小范围重构解决的问题，不要夸张为架构级缺陷。",
            ],
            dimensions=[
                auto_dimension(
                    name="复杂度与职责切分",
                    objective="确认方法和类的复杂度没有膨胀到影响理解和修改安全性。",
                    repo_focus="长方法、深层 if/else、复杂条件、超大类、职责散布。",
                    topics=[
                        "方法是否承担了多个不同意图的步骤",
                        "条件分支是否过深且难以局部验证",
                        "一个类是否同时负责计算、装配、调用和错误处理",
                        "布尔参数或 mode 参数是否隐藏了多种职责",
                    ],
                    anti_patterns=[
                        "一个方法几十行到上百行，混合校验、编排、转换和副作用。",
                        "新增一个场景只能继续叠 if/else。",
                        "相同依赖在多个分支里反复调用，没人看得出主路径。",
                        "类名很抽象，但实际上什么都做。",
                    ],
                    questions=[
                        "这段代码能否按“准备数据、核心决策、副作用提交”拆开？",
                        "是否存在一个参数在切换完全不同的行为？",
                        "局部改一个分支时，是否容易破坏其他分支？",
                        "读者能否在一分钟内看懂主路径？",
                    ],
                    good_signal="主路径清晰、职责单一、局部改动能被局部理解。",
                    bad_signal="代码理解依赖作者脑补，稍改就担心连锁反应。",
                    repair_pattern="拆方法、提炼意图命名、消除 mode 参数、缩小类职责。",
                ),
                auto_dimension(
                    name="重复与局部抽象",
                    objective="确认相同业务或技术意图没有以复制粘贴形式扩散。",
                    repo_focus="重复分支、重复 mapper、重复异常映射、重复日志模板、重复配置解析。",
                    topics=[
                        "相同业务规则是否在多个入口重复实现",
                        "相同转换逻辑是否存在多个轻微变体",
                        "相同异常映射和日志模板是否被手写复制",
                        "重复代码背后是否需要抽象而不是继续复制",
                    ],
                    anti_patterns=[
                        "复制一份旧分支改两个字段就上线。",
                        "每个 handler 各写一份几乎相同的校验和日志。",
                        "相同 mapper 在三个文件各写一版。",
                        "相同常量或配置键散落多个地方。",
                    ],
                    questions=[
                        "如果这条规则还要再改一次，要改几个地方？",
                        "这几段相似代码真正共性的意图是什么？",
                        "抽象后会更清晰，还是只是隐藏差异？",
                        "哪些重复应该保留，哪些重复已经形成维护债？",
                    ],
                    good_signal="重复被收敛到清晰可理解的抽象，而不是魔法基类。",
                    bad_signal="每来一个新场景就复制一段旧代码改词。",
                    repair_pattern="抽出共享意图，保留必要差异点，避免过度抽象。",
                ),
                auto_dimension(
                    name="命名与可读性",
                    objective="确认命名、日志、局部变量和注释能帮助维护者快速理解意图。",
                    repo_focus="类名、方法名、变量名、日志、异常信息、注释与 TODO。",
                    topics=[
                        "命名是否表达真实意图而不是技术细节",
                        "日志和异常是否能帮助定位问题而不是制造噪音",
                        "注释是否解释了为什么，而不是重复代码表面动作",
                        "局部变量是否帮助分段理解主路径",
                    ],
                    anti_patterns=[
                        "名字像 process/doHandle/commonData 这类看不出意图的命名。",
                        "异常日志全是 print stacktrace，没有业务上下文。",
                        "注释只是把代码再读一遍。",
                        "临时变量和布尔值命名完全依赖上下文猜测。",
                    ],
                    questions=[
                        "这个名字是否能让新同事一眼知道它做什么？",
                        "日志里是否保留了足够的业务上下文和标识？",
                        "注释是否真的解释了约束和原因？",
                        "能否通过提炼变量或方法让代码自解释？",
                    ],
                    good_signal="命名稳定、日志有上下文、注释只解释难点和原因。",
                    bad_signal="读者需要来回跳文件才能猜出名字含义。",
                    repair_pattern="重命名、补上下文日志、删除无效注释并提炼意图方法。",
                ),
                auto_dimension(
                    name="副作用可见性",
                    objective="确认副作用位置明显、顺序清楚，调用者不会被隐藏行为偷袭。",
                    repo_focus="隐式缓存写入、静态状态、全局配置、工具类副作用、隐式 IO。",
                    topics=[
                        "方法名是否掩盖了真实副作用",
                        "工具类或 helper 是否暗中写状态、发消息、打日志",
                        "是否存在隐式依赖系统时钟、线程上下文或全局变量",
                        "调用者是否能从签名和名称看出后果",
                    ],
                    anti_patterns=[
                        "看起来像 getter 的方法其实会写库或发消息。",
                        "helper 内部偷偷依赖 ThreadLocal 和全局配置。",
                        "工具方法顺手刷缓存或发监控，没有暴露在接口上。",
                        "调用顺序稍变就引入隐藏副作用。",
                    ],
                    questions=[
                        "调用这个方法的人是否知道它会产生哪些副作用？",
                        "副作用是否可以显式上提到调用链上层？",
                        "是否依赖隐式上下文而非显式参数？",
                        "能否通过接口命名或返回值让副作用更可见？",
                    ],
                    good_signal="副作用通过命名、参数或返回值显式暴露。",
                    bad_signal="方法看起来纯净，实际却藏着多个写操作。",
                    repair_pattern="显式化副作用、拆分纯计算与副作用提交、减少全局状态依赖。",
                ),
                auto_dimension(
                    name="可测试性前提",
                    objective="确认代码结构没有让后续补测试变得异常昂贵。",
                    repo_focus="静态依赖、构造复杂度、时间依赖、随机性、IO 隐藏点。",
                    topics=[
                        "依赖是否容易注入和替换",
                        "时间、随机数、外部 IO 是否可控",
                        "核心逻辑是否能在无框架环境下被直接调用",
                        "构造测试输入是否需要大量样板对象",
                    ],
                    anti_patterns=[
                        "逻辑全写在 static 工具类里，很难替换依赖。",
                        "直接调用系统时钟、UUID、环境变量，测试不可预测。",
                        "测试一个分支需要堆十几层对象。",
                        "核心逻辑散落在注解驱动的回调里，难以单独触发。",
                    ],
                    questions=[
                        "如果明天要补单测，这段代码最难测的点是什么？",
                        "是否可以把时间、随机性和 IO 提升成可注入依赖？",
                        "是否可以把核心逻辑从框架回调中提出来？",
                        "测试准备数据是否比真正断言还复杂？",
                    ],
                    good_signal="核心逻辑易调用、依赖可替换、测试构造成本低。",
                    bad_signal="任何测试都要起大半个系统或堆很多样板。",
                    repair_pattern="拆出纯逻辑、显式注入依赖、减少静态和隐式上下文。",
                ),
            ],
            false_positives=[
                "不要把个人风格差异当成可维护性缺陷，重点看维护成本是否真实升高。",
                "不要把所有大方法都一刀切定性，先看复杂度是否真的影响理解和改动安全性。",
                "不要把业务重复和架构重复混成一类，先界定重复发生在哪层。",
                "不要越界判断业务正确性或性能问题。",
                "不要把“代码短”误解为“代码易维护”。",
            ],
            java_focus=[
                "检查 Spring 组件是否把核心逻辑写死在注解回调和静态工具里。",
                "检查异常、日志、配置读取和 mapper 是否重复散落。",
                "检查是否依赖系统时钟、UUID、环境变量等不可控输入。",
                "检查构造器注入是否清晰表达真实依赖数量。",
                "检查 util/common/helper 是否成为隐藏职责垃圾桶。",
            ],
            scenarios=[
                {
                    "name": "复制旧分支改几个字段",
                    "change": "MR 为了新场景直接复制旧 handler/service。",
                    "avoid": "不要只说重复，要说明未来改动需要同步多少处。",
                    "inspect": "搜索相似方法、相同日志和相同异常分支。",
                    "evidence": "指出至少两处重复代码和它们未来必然同步维护的证据。",
                    "bad_conclusion": "“复制也没关系，先能跑”。",
                    "repair": "建议抽共享主路径，只保留场景差异点作为参数或策略。",
                },
                {
                    "name": "方法过长且副作用混杂",
                    "change": "MR 在原有长方法上继续追加分支和写操作。",
                    "avoid": "不要只统计行数，要指出理解和测试为什么变难。",
                    "inspect": "看主路径、副作用点和重复条件。",
                    "evidence": "说明局部改动无法局部验证的具体原因。",
                    "bad_conclusion": "“超过 50 行就是错”。",
                    "repair": "建议按意图拆分并显式隔离副作用。",
                },
                {
                    "name": "命名掩盖真实行为",
                    "change": "MR 新增一个名字像 query/get 的方法，实际会写状态。",
                    "avoid": "不要只说名字不好，要指出调用者会被误导。",
                    "inspect": "看方法体里的写操作、缓存刷新、事件发布。",
                    "evidence": "指出调用点为什么会误判副作用。",
                    "bad_conclusion": "“重命名一下就行”。",
                    "repair": "建议拆分纯读取与副作用操作，并重新命名接口。",
                },
                {
                    "name": "测试构造成本飙升",
                    "change": "MR 引入更多静态调用和隐式依赖。",
                    "avoid": "不要替测试专家评价覆盖率，只看结构是否难测。",
                    "inspect": "看是否依赖系统时钟、环境变量、静态单例。",
                    "evidence": "说明补一个简单测试需要哪些困难准备。",
                    "bad_conclusion": "“以后再补测试”。",
                    "repair": "建议把不可控依赖抽出来，通过接口或参数注入。",
                },
                {
                    "name": "helper 吞掉关键语义",
                    "change": "MR 把一堆业务逻辑塞进 common helper。",
                    "avoid": "不要只说 helper 不好，要指出职责与调用可见性问题。",
                    "inspect": "看 helper 被谁调用、依赖什么上下文、产生什么副作用。",
                    "evidence": "指出 helper 成为隐式业务中心的证据。",
                    "bad_conclusion": "“公共方法越多越方便”。",
                    "repair": "建议把 helper 拆回明确所属模块或策略对象。",
                },
                {
                    "name": "注释替代代码表达",
                    "change": "MR 通过大量注释解释复杂逻辑，但不做重构。",
                    "avoid": "不要一概反注释，要区分解释原因和掩盖复杂度。",
                    "inspect": "看注释是否在替代更清晰的结构和命名。",
                    "evidence": "指出读者仍然无法仅凭代码理解主路径。",
                    "bad_conclusion": "“有注释就够了”。",
                    "repair": "建议重构为自解释代码，仅保留解释原因与边界的注释。",
                },
            ],
            final_checklist=[
                "是否明确指出了维护成本如何上升，而不是只做审美批评？",
                "是否给出了重复、复杂度或隐藏副作用的直接证据？",
                "是否避免越界去评价业务、安全、数据库问题？",
                "是否给出了小步可实施的重构建议？",
                "是否兼顾了可读性和可测试性两个维度？",
            ],
            glossary=[
                "代码健康: 代码被阅读、修改、测试和演进的综合成本。",
                "隐藏副作用: 接口表面看不出来，但执行时会产生写操作或外部动作。",
                "局部抽象: 围绕当前上下文提炼的最小有用抽象，而非全局框架化。",
                "样板成本: 为了跑通或测试一小段逻辑而必须构造的大量无关代码。",
            ],
        ),
        with_common_lists(
            expert_id="security_compliance",
            title="安全与合规专家代码检视规范",
            role="面向认证、授权、输入校验、敏感数据与审计的安全审视专家",
            mission="识别认证授权缺陷、输入处理漏洞、数据暴露风险和审计缺失。",
            sources=[
                "[OWASP ASVS](https://owasp.org/www-project-application-security-verification-standard/)",
                "[OWASP Code Review Guide](https://owasp.org/www-project-code-review-guide/)",
                "[OWASP Top 10](https://owasp.org/www-project-top-ten/)",
                "[Spring Security Reference](https://docs.spring.io/spring-security/reference/)",
            ],
            in_scope=[
                "身份认证、会话、token/cookie、机器身份和回调认证。",
                "接口级授权、对象级授权、租户隔离和越权访问风险。",
                "输入校验、反序列化、注入、文件上传和输出编码。",
                "敏感数据处理、日志脱敏、审计留痕和安全事件可追踪性。",
            ],
            out_scope=[
                "不负责模块分层和架构演进，这属于架构专家。",
                "不负责查询计划、索引与事务细节，这属于数据库专家。",
                "不负责纯业务语义是否正确，这属于正确性专家。",
                "不负责测试分层设计，这属于测试专家。",
            ],
            handoff=[
                "看到数据库锁、索引和 schema 约束时移交数据库专家。",
                "看到业务状态语义偏差时移交正确性专家。",
                "看到性能瓶颈、资源耗尽和降级策略时移交性能专家。",
                "看到架构越界或上下文耦合时移交架构专家。",
            ],
            repo_queries=[
                "搜索鉴权注解、安全配置、filter/interceptor、permission evaluator。",
                "搜索对象加载后是否再次做所有权或租户校验。",
                "搜索输入校验、反序列化、SQL/SpEL/模板/脚本拼接点。",
                "搜索日志、异常、监控和审计事件中的敏感字段输出。",
                "搜索 webhook/callback、签名校验、机器身份与内部接口保护。",
            ],
            evidence=[
                "必须指出可被利用的路径或缺失的保护点，而不是只说“可能不安全”。",
                "授权问题必须给出对象、租户或角色边界被绕过的证据。",
                "敏感数据问题必须指出字段名、输出位置和泄露对象。",
                "若证据不足，只能写待验证风险，不能空口宣称存在漏洞。",
            ],
            dimensions=[
                auto_dimension(
                    name="认证与会话",
                    objective="确认登录态、token、cookie、回调和机器身份校验可靠。",
                    repo_focus="auth filter、security config、token parser、callback/webhook verifier。",
                    topics=[
                        "登录态校验是否在正确边界执行且不可绕过",
                        "token/cookie 是否完整校验签名、时效和主体",
                        "回调或 webhook 是否验证来源和重放风险",
                        "机器身份和内部接口是否仍然受到保护",
                    ],
                    anti_patterns=[
                        "只校验 token 存在，不校验主体、签名或时效。",
                        "callback 只看 shared secret 字段存在，不验签。",
                        "内部接口默认信任内网，不做身份校验。",
                        "刷新 token 和登出语义不完整，旧 token 继续可用。",
                    ],
                    questions=[
                        "当前入口是谁在认证，谁在信任认证结果？",
                        "旧 token、过期 token、跨租户 token 是否都被拒绝？",
                        "webhook 是否可被重放或伪造？",
                        "内部接口的机器身份来自哪里、如何轮换？",
                    ],
                    good_signal="身份校验完整、来源可验证、会话失效语义明确。",
                    bad_signal="只要请求到了服务就默认可信，或把来源可信建立在网络位置上。",
                    repair_pattern="补全身份验证、签名校验、失效处理和重放防护。",
                ),
                auto_dimension(
                    name="授权与对象级访问控制",
                    objective="确认请求主体只能访问自己有权访问的对象和租户数据。",
                    repo_focus="permission check、tenant filter、ownership 校验、object load 后校验。",
                    topics=[
                        "接口级授权是否存在且不依赖调用方自觉",
                        "对象加载后是否再次做所有权/租户校验",
                        "批量操作是否对每个对象都校验权限",
                        "后台任务和内部接口是否也执行最小权限原则",
                    ],
                    anti_patterns=[
                        "先按 id 查到对象，再直接返回给当前用户。",
                        "列表接口做了租户过滤，详情接口忘了做。",
                        "批量操作只校验一次角色，不校验每个对象归属。",
                        "内部管理接口默认超级权限，缺少边界保护。",
                    ],
                    questions=[
                        "攻击者若知道另一个对象 ID，会不会直接读到？",
                        "跨租户、跨组织、跨项目数据是否仍被过滤？",
                        "角色校验和对象归属校验是否都在？",
                        "后台任务是否可能读取到超出授权范围的数据？",
                    ],
                    good_signal="接口级与对象级授权同时存在，租户边界稳定。",
                    bad_signal="只校验角色，不校验对象归属和租户边界。",
                    repair_pattern="补接口级和对象级校验，并把租户过滤收口到可靠入口。",
                ),
                auto_dimension(
                    name="输入处理与注入防护",
                    objective="确认外部输入在到达核心逻辑前经过充分校验、约束和安全处理。",
                    repo_focus="validation、deserialization、query build、template/script/file upload。",
                    topics=[
                        "输入校验是否覆盖格式、范围、长度和枚举约束",
                        "动态拼接 SQL、SpEL、模板或脚本是否可控",
                        "反序列化、文件上传和路径处理是否有额外风险",
                        "异常返回和错误信息是否泄露过多实现细节",
                    ],
                    anti_patterns=[
                        "把原始字符串直接拼进 SQL、表达式或模板。",
                        "依赖前端校验，后端完全不验。",
                        "文件上传只看扩展名，不看内容类型和存储路径。",
                        "异常信息把内部路径、SQL、秘钥名全打给调用方。",
                    ],
                    questions=[
                        "这个输入来自哪里，是否被多个边界重复利用？",
                        "这里是否存在任何拼接式执行？",
                        "上传内容会不会落到可执行路径或可公开访问位置？",
                        "错误返回里暴露了哪些不该让攻击者知道的信息？",
                    ],
                    good_signal="输入有明确约束，执行边界不接受原始拼接，错误信息克制。",
                    bad_signal="默认信任客户端输入，或把所有值原样透传给底层执行器。",
                    repair_pattern="补校验、参数化、白名单和安全错误处理，避免直接拼接执行。",
                ),
                auto_dimension(
                    name="敏感数据与审计",
                    objective="确认敏感数据暴露最小化、日志脱敏和安全审计完整。",
                    repo_focus="log、audit、monitor、trace、response mask、storage encryption hint。",
                    topics=[
                        "日志、监控、异常和 trace 中是否输出敏感字段",
                        "接口返回是否暴露超出最小必要范围的数据",
                        "安全关键动作是否有可追踪审计记录",
                        "敏感配置、密钥和凭证是否被错误落盘或打印",
                    ],
                    anti_patterns=[
                        "把 token、密码、手机号、身份证、银行卡写进日志。",
                        "调试方便临时把完整对象输出到 response 或监控。",
                        "安全关键操作没有审计事件，事后无法追踪。",
                        "密钥、连接串和私钥直接进配置文件或异常信息。",
                    ],
                    questions=[
                        "真正需要看这个字段的人是谁，当前输出是否超范围？",
                        "日志脱敏是否覆盖所有关键入口？",
                        "管理员操作、权限变更、登录失败是否有审计轨迹？",
                        "异常和 debug 输出是否把密钥材料泄露出去了？",
                    ],
                    good_signal="最小暴露、默认脱敏、关键动作可追踪。",
                    bad_signal="为了排障把敏感数据无差别打进日志和响应。",
                    repair_pattern="收缩返回字段、补脱敏和审计事件、清理密钥暴露路径。",
                ),
                auto_dimension(
                    name="合规与安全基线",
                    objective="确认安全控制不是零散补丁，而是符合稳定基线和可核查要求。",
                    repo_focus="security baseline、headers、cors、csrf、rate limit、retention、deletion path。",
                    topics=[
                        "安全头、CORS、CSRF、限流和风控基线是否被破坏",
                        "数据保留、删除和导出是否有边界控制",
                        "是否存在默认开放或临时放开的后门配置",
                        "合规要求需要的告知、授权和留痕是否仍然存在",
                    ],
                    anti_patterns=[
                        "为了联调临时关闭安全头或扩大 CORS，结果直接上线。",
                        "删除接口只做逻辑删除，却没有真正的数据保留策略说明。",
                        "debug 开关能直接绕过安全检查。",
                        "安全相关配置散落多处，没人知道最终生效值。",
                    ],
                    questions=[
                        "当前改动有没有降低系统默认安全基线？",
                        "调试开关或白名单是否会带到生产？",
                        "数据删除、导出、共享是否满足最小权限与留痕要求？",
                        "安全配置的单一真实来源在哪里？",
                    ],
                    good_signal="安全基线集中、默认收紧、调试例外可控且可审计。",
                    bad_signal="上线靠约定记住要关开关，代码里没有真实约束。",
                    repair_pattern="把安全基线收口成默认配置，并为例外场景补显式白名单与审计。",
                ),
            ],
            false_positives=[
                "不要把普通业务校验误报成安全问题，只有攻击者可利用或边界失守时才升级。",
                "不要看到字符串拼接就下结论，先看是否进入可执行边界。",
                "不要把内部调试日志一律判定为泄露，先看环境、对象和字段范围。",
                "不要代替架构或数据库专家判断他们的专属问题。",
                "不要把“缺少安全注解”当作充分证据，必须看真实保护链路。",
            ],
            java_focus=[
                "检查 Spring Security 配置、method security、permission evaluator 和 filter 链。",
                "检查 Bean Validation、Jackson 反序列化、SpEL、模板渲染与 query 参数化。",
                "检查日志框架、异常处理、审计事件和 trace context 的敏感字段输出。",
                "检查 Webhook/Callback 的签名、时间戳和重放防护。",
                "检查 CORS、CSRF、rate limit、debug 开关和环境差异配置。",
            ],
            scenarios=[
                {
                    "name": "详情接口缺少对象级授权",
                    "change": "MR 新增按 id 查询详情接口。",
                    "avoid": "不要只看角色注解，要看对象归属和租户边界。",
                    "inspect": "搜对象加载后校验、tenant filter、ownership 检查。",
                    "evidence": "说明已登录但无权用户如何利用 id 读取他人数据。",
                    "bad_conclusion": "“有 @PreAuthorize 就安全”。",
                    "repair": "建议在对象加载后补归属校验，并统一租户过滤入口。",
                },
                {
                    "name": "Webhook 只校验来源 IP",
                    "change": "MR 新增 webhook 入口。",
                    "avoid": "不要把内网或 IP 白名单当作充分认证。",
                    "inspect": "看签名、时间戳、nonce、重放处理和错误响应。",
                    "evidence": "指出伪造或重放的可行路径。",
                    "bad_conclusion": "“只有可信服务会调这个接口”。",
                    "repair": "建议补签名校验、时间窗口和重放保护。",
                },
                {
                    "name": "异常日志打印敏感对象",
                    "change": "MR 为排障直接打印完整请求对象。",
                    "avoid": "不要只说日志多，要指出具体敏感字段和暴露范围。",
                    "inspect": "看日志模板、对象字段、异常链和 trace 输出。",
                    "evidence": "指出密码、token、手机号等字段实际进入日志的位置。",
                    "bad_conclusion": "“测试环境无所谓”。",
                    "repair": "建议按字段脱敏并缩小日志内容到必要上下文。",
                },
                {
                    "name": "动态查询拼接",
                    "change": "MR 用字符串拼接 query 或表达式。",
                    "avoid": "不要仅凭字符串拼接就下死结论，要看是否进入执行边界。",
                    "inspect": "看参数来源、执行器、参数化能力和白名单。",
                    "evidence": "说明输入如何被拼进 SQL、SpEL、模板或脚本。",
                    "bad_conclusion": "“看起来像注入，但也许不会执行”。",
                    "repair": "建议改为参数化或白名单映射，不直接拼执行表达式。",
                },
                {
                    "name": "调试开关绕过鉴权",
                    "change": "MR 为联调加入 bypass flag。",
                    "avoid": "不要把它当普通 feature flag，看是否能在生产生效。",
                    "inspect": "搜配置来源、环境判断、默认值和审计。",
                    "evidence": "指出何种环境下可能被误打开或长期残留。",
                    "bad_conclusion": "“上线前记得关就行”。",
                    "repair": "建议删除后门或至少加环境硬约束和审计。",
                },
                {
                    "name": "批量接口只校验一次角色",
                    "change": "MR 新增批量修改接口。",
                    "avoid": "不要只看入口角色，要看每个对象归属。",
                    "inspect": "搜循环内校验、租户过滤和对象加载逻辑。",
                    "evidence": "说明用户如何借批量接口改到不属于自己的对象。",
                    "bad_conclusion": "“管理员角色足够了”。",
                    "repair": "建议对每个对象做归属校验，并拒绝跨租户混合输入。",
                },
            ],
            final_checklist=[
                "是否明确给出了攻击或绕过路径，而不是笼统说“可能不安全”？",
                "是否给出了对象、字段、接口或租户边界的具体证据？",
                "是否避免越界去评价业务或数据库问题？",
                "是否给出了收缩攻击面和验证修复的具体做法？",
                "是否区分了直接漏洞与待验证安全风险？",
            ],
            glossary=[
                "对象级授权: 对单个资源实例的归属或权限检查。",
                "最小暴露: 只向必要主体暴露必要数据和必要接口。",
                "重放攻击: 合法请求被复制后再次提交以重复触发动作。",
                "安全基线: 系统默认必须满足的最小安全控制集合。",
            ],
        ),
    ]
)

PROFILES.extend(
    [
        with_common_lists(
            expert_id="ddd_specification",
            title="DDD 规范专家代码检视规范",
            role="面向聚合、上下文边界与领域语言的 DDD 审视专家",
            mission="识别聚合边界失真、领域服务滥用、仓储抽象失真和上下文耦合问题。",
            sources=[
                "[Microsoft microservices guide: domain analysis and bounded context](https://learn.microsoft.com/azure/architecture/microservices/model/domain-analysis)",
                "[Spring Data domain events reference](https://docs.spring.io/spring-data/commons/reference/repositories/core-domain-events.html)",
                "[Spring Modulith Reference](https://docs.spring.io/spring-modulith/reference/)",
            ],
            in_scope=[
                "聚合边界、实体和值对象职责、领域服务和应用服务职责划分。",
                "bounded context 之间的模型边界、ACL、防腐层和共享语言。",
                "仓储接口是否表达聚合意图，而不是暴露技术实现。",
                "领域事件的产生、命名和边界含义是否清晰。",
            ],
            out_scope=[
                "不负责 SQL、索引、migration，这属于数据库专家。",
                "不负责鉴权漏洞定性，这属于安全专家。",
                "不负责纯代码可读性与命名微调，这属于可维护性专家。",
                "不负责测试设计完整性，这属于测试专家。",
            ],
            handoff=[
                "看到控制器/配置类越界时移交架构专家。",
                "看到事务锁、表结构和查询问题时移交数据库专家。",
                "看到权限和租户隔离时移交安全专家。",
                "看到测试不能保护聚合不变量时移交测试专家。",
            ],
            repo_queries=[
                "搜索 aggregate、entity、value object、domain service、repository 接口。",
                "搜索同一业务术语是否在多个模块出现不同模型定义。",
                "搜索跨模块调用是否直接传递内部实体或枚举。",
                "搜索 domain event、outbox、consumer 是否表达了清晰领域事实。",
                "搜索 application service 是否吸收了本应属于聚合的不变量。",
            ],
            evidence=[
                "必须指出哪个领域概念被放错层，或者哪个聚合边界被穿透。",
                "不能把普通分层问题都当成 DDD 问题，必须能映射到聚合、上下文或领域语言。",
                "若只是代码风格更像贫血模型，但未破坏不变量，只能降级为 design_concern。",
                "跨上下文问题必须至少给出两处模型或调用证据。",
            ],
            dimensions=[
                auto_dimension(
                    name="聚合边界",
                    objective="确认不变量被聚合持有，跨聚合写操作不会偷偷发生。",
                    repo_focus="aggregate、entity、repository、command handler、domain method。",
                    topics=[
                        "聚合根是否真正拥有关键不变量",
                        "是否存在绕过聚合根直接改子实体状态",
                        "跨聚合更新是否通过应用服务协调而非互相穿透",
                        "仓储接口是否以聚合意图为单位暴露能力",
                    ],
                    anti_patterns=[
                        "repository 直接暴露子实体 update，让调用方绕过聚合根。",
                        "一个聚合同时承载多个独立业务生命周期。",
                        "跨聚合事务里随手改多个实体字段，没有显式协调逻辑。",
                        "聚合根只是数据袋，不负责任何不变量。",
                    ],
                    questions=[
                        "哪个对象真正拥有这条业务规则？",
                        "如果跳过聚合根直接写字段，会不会破坏不变量？",
                        "这次改动是不是把两个聚合偷偷并成一个了？",
                        "仓储接口表达的是业务操作还是技术 CRUD？",
                    ],
                    good_signal="不变量落在聚合根内，跨聚合协作用应用服务或领域事件承接。",
                    bad_signal="任何调用方都能改关键字段，聚合根形同虚设。",
                    repair_pattern="收口写入口到聚合根或聚合服务，并通过应用层协调跨聚合流程。",
                ),
                auto_dimension(
                    name="领域服务与应用服务",
                    objective="确认应用服务只编排流程，领域服务只承载真正跨实体的领域规则。",
                    repo_focus="application service、domain service、usecase、orchestrator。",
                    topics=[
                        "应用服务是否吞掉了本该在聚合内维护的不变量",
                        "领域服务是否真的跨多个聚合或值对象才存在",
                        "是否把技术校验、协议映射误塞进领域服务",
                        "长 workflow 是否被误称为领域服务以规避设计问题",
                    ],
                    anti_patterns=[
                        "领域服务里满是 HTTP DTO、事务注解和远端客户端。",
                        "应用服务有大量业务规则判断，却没有任何聚合方法。",
                        "为了避免改聚合，把所有规则都堆到 service 里。",
                        "一个所谓 domain service 同时编排消息、缓存和权限。",
                    ],
                    questions=[
                        "这条规则属于某个聚合自身，还是跨聚合协作规则？",
                        "如果去掉 Spring 注解和远端调用，这个服务还剩下多少领域逻辑？",
                        "应用层是否只是组织步骤，还是已经在决定业务真相？",
                        "是否应该把规则推回实体/值对象？",
                    ],
                    good_signal="应用服务组织步骤，领域服务只保留真正的领域决策。",
                    bad_signal="service 名字是 domain，内容却全是技术和流程编排。",
                    repair_pattern="把不变量推回聚合，把跨聚合规则留给领域服务，把流程编排留给应用层。",
                ),
                auto_dimension(
                    name="上下文边界",
                    objective="确认 bounded context 没有通过内部模型直接耦合。",
                    repo_focus="module、package、API contract、ACL、translator、anti-corruption layer。",
                    topics=[
                        "跨上下文是否直接传递内部 entity/enum",
                        "共享语言与内部实现模型是否被混用",
                        "防腐层是否存在且真正完成了翻译",
                        "一个上下文的变更是否逼迫另一个上下文同步重构",
                    ],
                    anti_patterns=[
                        "两个模块直接共享 entity 和 repository。",
                        "外部上下文的字段名直接渗进本域模型。",
                        "所谓 ACL 只是把外部 DTO 原样传进来。",
                        "上下文边界靠包名分隔，但运行时完全耦合。",
                    ],
                    questions=[
                        "这里传递的是共享语言，还是内部实现模型？",
                        "另一个上下文改字段名时，这里会不会一起崩？",
                        "翻译层是否真的做了概念转换？",
                        "是否有必要把概念隔离成独立 contract？",
                    ],
                    good_signal="上下文只通过公开契约交互，内部模型保持自治。",
                    bad_signal="外部概念、枚举、异常直接渗入本域内部逻辑。",
                    repair_pattern="建立或补强 ACL/translator，停止跨上下文直接传实体。",
                ),
                auto_dimension(
                    name="领域事件",
                    objective="确认事件表达的是领域事实，而不是技术细节或半成品状态。",
                    repo_focus="domain event、outbox、publisher、consumer、event naming。",
                    topics=[
                        "事件命名是否表达已经发生的业务事实",
                        "事件载荷是否携带稳定领域语义而非内部实现细节",
                        "事件发布时间是否与聚合提交语义一致",
                        "消费者是否依赖了不该暴露的内部字段",
                    ],
                    anti_patterns=[
                        "事件名字像 SaveDone、SyncTriggered 这类技术动作而非业务事实。",
                        "事件在事务未提交前发布，消费者读不到一致视图。",
                        "消费者强依赖 entity 全量快照。",
                        "事件字段没有语义说明，只是顺手把内部 DTO 全抛出去。",
                    ],
                    questions=[
                        "这个事件是业务事实，还是技术过程中的中间动作？",
                        "消费者是否只依赖稳定领域字段？",
                        "如果事件重放，语义还能保持一致吗？",
                        "发布时机是否与事务提交一致？",
                    ],
                    good_signal="事件命名稳定、语义清晰、发布时间与业务提交一致。",
                    bad_signal="把内部中间态当事件发出去，导致消费者绑定技术细节。",
                    repair_pattern="重命名事件、收缩载荷、把发布时间与提交边界对齐。",
                ),
                auto_dimension(
                    name="仓储与领域语言",
                    objective="确认仓储接口和方法名表达业务语言，而不是裸技术 CRUD。",
                    repo_focus="repository 接口、query object、specification、aggregate load/save。",
                    topics=[
                        "仓储方法是否以业务意图命名而不是技术动词堆砌",
                        "是否把跨聚合查询和报表查询硬塞进聚合仓储",
                        "仓储是否暴露了不该被外界依赖的内部查询细节",
                        "领域语言在仓储、服务和事件中是否前后一致",
                    ],
                    anti_patterns=[
                        "仓储接口像万能 DAO，任何表都往里塞。",
                        "报表查询和聚合加载写在同一个 repository。",
                        "方法名是 findByXAndYAndZ，但没人知道业务意图。",
                        "同一业务概念在不同层分别叫三种名字。",
                    ],
                    questions=[
                        "仓储方法名能否直接让领域专家听懂意图？",
                        "这条查询是聚合加载，还是报表/投影需求？",
                        "为什么这个仓储要暴露底层技术细节？",
                        "领域术语是否在接口、事件和文档中保持一致？",
                    ],
                    good_signal="仓储围绕聚合意图建模，查询职责与报表职责分开。",
                    bad_signal="仓储退化成全能 DAO，领域语言消失不见。",
                    repair_pattern="按聚合和用途拆仓储/查询接口，并统一领域术语。",
                ),
            ],
            false_positives=[
                "不要把所有分层问题都贴上 DDD 标签，只有聚合、上下文、领域语言失真才算。",
                "不要因为实体没有很多方法就直接判贫血模型，先看不变量是否真的丢失。",
                "不要把普通事件总线使用问题误判为领域事件设计问题。",
                "不要代替架构专家评价所有模块依赖，只看 bounded context 与聚合边界。",
                "不要代替数据库专家评价仓储里的 SQL 细节。",
            ],
            java_focus=[
                "检查 Spring Modulith 或模块边界是否与 bounded context 一致。",
                "检查 repository 是否围绕 aggregate load/save 建模。",
                "检查 domain event 是否在事务提交语义后发布。",
                "检查 application service 是否只编排 command，而非承载核心不变量。",
                "检查 entity/value object 是否拥有真正的领域表达力。",
            ],
            scenarios=[
                {
                    "name": "子实体被外部直接 update",
                    "change": "MR 新增 repository 方法直接更新子实体字段。",
                    "avoid": "不要只批评抽象不优雅，要指出这会绕过哪个聚合不变量。",
                    "inspect": "搜聚合根方法、子实体关系和所有写入口。",
                    "evidence": "证明外部调用可以在不经过聚合根的情况下破坏规则。",
                    "bad_conclusion": "“Repository 不该 update”这种空泛意见。",
                    "repair": "建议把写入口收回聚合根或领域服务，并保留显式意图方法。",
                },
                {
                    "name": "跨上下文共享内部枚举",
                    "change": "MR 让两个模块直接复用同一内部 enum。",
                    "avoid": "不要只说依赖不好，要说明上下文语言被绑定了。",
                    "inspect": "搜 enum 定义、外部契约和 translator/ACL。",
                    "evidence": "指出另一个上下文被迫理解本域内部状态的具体位置。",
                    "bad_conclusion": "“枚举共享一定有问题”。",
                    "repair": "建议引入稳定契约或 translator，而不是直接共享内部模型。",
                },
                {
                    "name": "应用服务吸走聚合规则",
                    "change": "MR 在 service 里加入大量状态判断和不变量校验。",
                    "avoid": "不要只说方法太长，要指出领域规则拥有者错了。",
                    "inspect": "搜聚合方法、原有不变量和 service 调用链。",
                    "evidence": "证明规则无法被实体自身维护，导致多入口不一致。",
                    "bad_conclusion": "“Service 里有 if 就不好”。",
                    "repair": "建议把核心规则推回聚合根，把 service 收敛成编排层。",
                },
                {
                    "name": "事件表达技术动作而非领域事实",
                    "change": "MR 新增某个 save/sync/update 事件。",
                    "avoid": "不要只盯命名，要看消费者绑定了什么语义。",
                    "inspect": "搜发布点、消费者、事件字段和事务边界。",
                    "evidence": "指出消费者在依赖内部过程，而不是稳定业务事实。",
                    "bad_conclusion": "“名字不好看”。",
                    "repair": "建议改成领域事实事件，并收缩到稳定字段。",
                },
                {
                    "name": "仓储变成万能 DAO",
                    "change": "MR 给聚合仓储继续塞入报表和联表查询。",
                    "avoid": "不要只从代码量角度评价，要指出领域语言被破坏。",
                    "inspect": "查看方法命名、返回类型和调用方用途。",
                    "evidence": "证明仓储已经无法表达聚合意图，只剩技术查询。",
                    "bad_conclusion": "“方法太多不好”。",
                    "repair": "建议拆出读模型查询接口，保留聚合仓储纯净性。",
                },
                {
                    "name": "共享语言与内部模型混用",
                    "change": "MR 把外部请求 DTO 直接塞进领域对象。",
                    "avoid": "不要只说耦合，要指出概念翻译缺失。",
                    "inspect": "查看 DTO、entity、translator 和应用服务边界。",
                    "evidence": "证明领域模型开始依赖外部协议字段。",
                    "bad_conclusion": "“DTO 和 entity 不能同名”。",
                    "repair": "建议加翻译层并恢复领域术语纯度。",
                },
            ],
            final_checklist=[
                "是否明确指出了聚合、上下文或领域语言具体哪里失真？",
                "是否避免把普通分层问题误报成 DDD 问题？",
                "是否给出了应移交给其他专家的信号？",
                "是否说明了更合理的聚合或上下文边界？",
                "是否给出了领域层面的修复建议而不是纯技术重构口号？",
            ],
            glossary=[
                "聚合: 共同维护一组业务不变量的对象边界。",
                "bounded context: 拥有独立模型和术语的上下文边界。",
                "共享语言: 团队和模型共同认可的业务术语体系。",
                "ACL: 保护本上下文不被外部模型污染的翻译层。",
                "领域事件: 已经发生的业务事实，而不是技术过程中的瞬时动作。",
            ],
        ),
    ]
)

PROFILES.extend(
    [
        with_common_lists(
            expert_id="database_analysis",
            title="数据库分析专家代码检视规范",
            role="Java 企业系统的数据库、ORM 与迁移审视专家",
            mission="识别 schema 演进、索引、事务、查询计划和 ORM 映射层面的数据库风险。",
            sources=[
                "[PostgreSQL Documentation](https://www.postgresql.org/docs/current/index.html)",
                "[MySQL 8.4 Reference Manual](https://dev.mysql.com/doc/refman/8.4/en/)",
                "[Spring Data JPA Reference](https://docs.spring.io/spring-data/jpa/reference/)",
                "[Flyway Documentation](https://documentation.red-gate.com/fd)",
                "[Hibernate ORM User Guide](https://docs.jboss.org/hibernate/orm/current/userguide/html_single/Hibernate_User_Guide.html)",
            ],
            in_scope=[
                "migration 脚本、DDL 兼容性、锁风险和大表改动策略。",
                "索引设计、查询条件、排序、分页和执行计划退化风险。",
                "事务传播、隔离级别、悲观/乐观锁和数据一致性边界。",
                "JPA/Hibernate/Spring Data 的实体映射、懒加载、级联和 N+1 风险。",
            ],
            out_scope=[
                "不负责业务状态语义是否正确，这属于正确性专家。",
                "不负责认证授权漏洞，这属于安全专家。",
                "不负责 Redis TTL 和缓存一致性，这属于 Redis 专家。",
                "不负责 MQ 投递语义，这属于 MQ 专家。",
            ],
            handoff=[
                "看到业务字段含义变化但数据库本身没问题时移交正确性专家。",
                "看到服务编排过重或边界错层时移交架构专家。",
                "看到慢查询主要来自调用风暴、重试放大时移交性能专家。",
                "看到测试缺少 migration 回归保护时移交测试专家。",
            ],
            repo_queries=[
                "搜索 migration、schema、entity、repository、specification、query builder。",
                "搜索新字段是否进入索引、唯一约束、排序、过滤和聚合查询。",
                "搜索事务注解、锁模式、批量更新和定时任务写库逻辑。",
                "搜索 entity 关系、fetch 策略、级联删除和 orphan removal。",
                "搜索同名列在读模型、报表 SQL、异步消费写库中的使用。",
            ],
            evidence=[
                "DDL 风险必须说明锁类型、重写成本或兼容性影响。",
                "查询风险必须指出查询条件、排序、join 或索引缺失如何退化。",
                "事务风险必须指出冲突窗口、一致性问题或死锁路径。",
                "ORM 风险必须指出实际会触发的懒加载、级联或脏数据场景。",
            ],
            dimensions=[
                auto_dimension(
                    name="schema 演进",
                    objective="确认表结构变更可在线演进、兼容旧数据并避免大面积阻塞。",
                    repo_focus="migration、DDL、nullable/default、索引与唯一约束。",
                    topics=[
                        "新增列时是否给出旧数据兼容策略和默认值语义",
                        "修改列类型时是否考虑历史数据与回滚难度",
                        "新增非空约束时是否先补数再收紧约束",
                        "删除或重命名列时是否考虑所有读链路和回放任务",
                    ],
                    anti_patterns=[
                        "直接给大表新增非空列且带默认值，导致表重写或长时间锁表。",
                        "先删列再改代码，导致旧任务和报表立即失效。",
                        "通过应用默认值掩盖历史脏数据，而不是清洗数据。",
                        "回滚脚本完全缺失，线上失败后无法收场。",
                    ],
                    questions=[
                        "旧数据没有这个字段时，查询和排序还能工作吗？",
                        "这条 DDL 在大表上会拿什么锁，锁多久？",
                        "迁移失败一半时，系统是否还能服务？",
                        "回滚路径是否和前滚路径一样明确？",
                    ],
                    good_signal="schema 变更分阶段发布，先兼容读写，再逐步收紧约束。",
                    bad_signal="DDL 一步到位、假设表小、假设旧数据干净、假设没人依赖旧列。",
                    repair_pattern="拆成多阶段迁移，先补数和兼容读写，再加约束和清理旧字段。",
                ),
                auto_dimension(
                    name="索引与查询",
                    objective="确认查询条件、排序和关联关系仍能被合理索引支撑。",
                    repo_focus="repository query、@Query、specification、分页、排序、报表 SQL。",
                    topics=[
                        "新增筛选条件是否有对应索引或复合索引顺序支撑",
                        "新增排序字段是否会触发 filesort 或全表扫描",
                        "count 查询是否被 join 或 distinct 放大成本",
                        "模糊查询和前缀查询是否还能命中索引策略",
                    ],
                    anti_patterns=[
                        "新接口看似只查单表，实际上排序字段完全没索引。",
                        "分页先全量拉回内存再排序分页。",
                        "为了方便一次性 join 多张大表，count 和 list 共用一条重 SQL。",
                        "复合索引顺序和 where/order by 完全不匹配。",
                    ],
                    questions=[
                        "这条新查询的主过滤条件是什么，索引是否覆盖？",
                        "分页深度变大后成本会不会陡增？",
                        "count 是否真的需要和 list 使用同一条 join 结构？",
                        "是否存在低选择性字段被错误放到索引前缀？",
                    ],
                    good_signal="查询模式和索引策略相互匹配，读写成本变化有解释。",
                    bad_signal="where/order by/group by 变化了，但索引和执行路径完全没考虑。",
                    repair_pattern="重写查询、补索引或拆分 list/count，避免把业务便利建立在全表扫描上。",
                ),
                auto_dimension(
                    name="事务与并发",
                    objective="确认事务范围、隔离语义和并发冲突处理没有被改坏。",
                    repo_focus="transactional、lock、version、retry、batch update、idempotent write。",
                    topics=[
                        "事务传播级别是否和业务一致性要求匹配",
                        "并发更新是否需要乐观锁或显式冲突处理",
                        "批量更新是否会绕开实体级不变量和审计字段",
                        "重试逻辑是否会放大写冲突或重复提交",
                    ],
                    anti_patterns=[
                        "跨远端调用的大事务导致锁持有时间显著变长。",
                        "批量 update 直接改状态，绕过版本号和更新时间。",
                        "重试只包数据库异常，但没有去重写入。",
                        "同一事务里读写顺序不稳定，容易死锁。",
                    ],
                    questions=[
                        "当前写路径在高并发下会不会互相覆盖？",
                        "是否需要版本号、唯一键或 select for update 来防并发冲突？",
                        "批量任务失败重跑会不会重复写入？",
                        "事务包住的代码里有哪些非数据库副作用？",
                    ],
                    good_signal="事务范围最小、并发冲突策略显式、重试与去重配套出现。",
                    bad_signal="把并发问题寄希望于低流量或运气，不做任何冲突控制。",
                    repair_pattern="缩小事务、补锁策略或版本控制，并把重试与去重一起设计。",
                ),
                auto_dimension(
                    name="ORM 映射",
                    objective="确认 entity、关联关系、fetch 策略和级联语义不会制造隐藏数据库问题。",
                    repo_focus="entity、relationship、fetch type、cascade、orphanRemoval、DTO projection。",
                    topics=[
                        "新增关联是否会引入 N+1 或大对象图加载",
                        "级联保存/删除是否会误伤不该跟随变化的数据",
                        "entity 字段默认值是否与数据库默认值一致",
                        "懒加载是否会在事务外或序列化时爆雷",
                    ],
                    anti_patterns=[
                        "toString/JSON 序列化意外触发懒加载，线上才发现。",
                        "新增 @OneToMany 后列表接口突然加载成百上千个子对象。",
                        "实体默认值和表默认值不一致，导致写入后回读不一样。",
                        "级联删除把共享数据一并删掉。",
                    ],
                    questions=[
                        "这个关联真的需要 entity 级别持有，还是查询投影更合适？",
                        "在事务外访问该字段会发生什么？",
                        "序列化或日志打印会不会触发整棵对象图加载？",
                        "级联策略是否与聚合边界一致？",
                    ],
                    good_signal="实体关系只表达必要聚合，查询使用投影，级联和 fetch 策略克制。",
                    bad_signal="一加字段就顺手挂到 entity，查询和序列化都被拖慢。",
                    repair_pattern="用 DTO/projection 承接查询，收紧关联和级联，只保留必要对象图。",
                ),
                auto_dimension(
                    name="读写链路一致性",
                    objective="确认数据库变更被所有写入、读取、投影和回放链路同步吸收。",
                    repo_focus="entity/repository/service/query/report/job/consumer 多端写读逻辑。",
                    topics=[
                        "新增列是否同步进入所有写入路径",
                        "新增列或约束是否同步进入报表和离线任务",
                        "读侧投影和缓存刷新是否理解新 schema",
                        "历史回放或补偿任务是否仍能读写新结构",
                    ],
                    anti_patterns=[
                        "主写链路改了，离线补数脚本仍写旧字段。",
                        "报表 SQL 没改，线上功能看起来正常但报表错了。",
                        "异步消费者还按旧 schema 写投影表。",
                        "回放任务重放旧消息时直接撞上新约束。",
                    ],
                    questions=[
                        "除了主写接口，哪些任务也会写这张表？",
                        "旧的投影、报表、定时任务是否都知道新字段？",
                        "回放旧消息或重放任务时会不会失败？",
                        "缓存或搜索索引是否也要一起更新？",
                    ],
                    good_signal="数据库结构变化伴随写侧、读侧、投影、回放链路一起更新。",
                    bad_signal="只看主业务接口通过，却忽略报表、补偿、异步和离线任务。",
                    repair_pattern="列出所有读写端并补齐联动修改，必要时做灰度兼容。",
                ),
            ],
            false_positives=[
                "不要把所有慢都归因于数据库，先确认是不是调用风暴或缓存失效导致。",
                "不要看到 migration 就自动判死刑，先结合表规模、分阶段策略和兼容路径。",
                "不要把 JPA 风格偏好当成数据库缺陷，必须指出真实查询或一致性风险。",
                "不要代替正确性专家定义业务语义是否正确，只评价数据库承载是否安全。",
                "不要只因为缺少索引名就下结论，重点是查询模式和执行风险。",
            ],
            java_focus=[
                "检查 Flyway/Liquibase migration 是否支持分阶段上线和回滚。",
                "检查 Spring Data JPA repository 查询是否与索引策略匹配。",
                "检查 @Transactional、Propagation、Isolation 是否符合一致性需求。",
                "检查 Hibernate 关联、fetch、cascade 是否克制。",
                "检查批量更新是否同步更新时间、版本号和审计字段。",
                "检查报表 SQL、异步消费者、离线任务是否同步理解新 schema。",
            ],
            scenarios=[
                {
                    "name": "新增 updatedAt 字段",
                    "change": "MR 在 schema 和 migration 中增加更新时间字段。",
                    "avoid": "不要只写“加索引/默认值”，要看全链路读写是否同步。",
                    "inspect": "搜索实体映射、transformer、排序查询、报表和补数任务。",
                    "evidence": "说明哪些路径会拿不到值、排序错或触发锁风险。",
                    "bad_conclusion": "“只是多一列，不会有问题”。",
                    "repair": "建议分阶段迁移、补回填脚本并同步所有读写链路。",
                },
                {
                    "name": "新增分页过滤条件",
                    "change": "MR 给列表接口增加新的 where 条件和排序。",
                    "avoid": "不要只看 SQL 能跑，要看索引和 count/list 成本。",
                    "inspect": "查看 repository query、order by、复合索引和 count 语句。",
                    "evidence": "给出执行路径退化或索引不匹配的直接说明。",
                    "bad_conclusion": "“where 子句不多，数据库肯定能顶住”。",
                    "repair": "建议补复合索引或拆 list/count 查询结构。",
                },
                {
                    "name": "批量任务更新状态",
                    "change": "MR 用批量 update 直接推进业务状态。",
                    "avoid": "不要只谈代码风格，要看是否绕过版本号和审计。",
                    "inspect": "搜索版本字段、更新时间、补偿逻辑和重复执行路径。",
                    "evidence": "证明批量写会导致审计丢失或并发覆盖。",
                    "bad_conclusion": "“批量 SQL 更快，所以没问题”。",
                    "repair": "建议补显式版本控制或拆成安全批处理策略。",
                },
                {
                    "name": "新增 entity 关联",
                    "change": "MR 在实体上挂了新的集合关联。",
                    "avoid": "不要只说 N+1，要看是否真的会进入查询和序列化链路。",
                    "inspect": "查看接口返回、日志打印、JSON 序列化和 fetch 策略。",
                    "evidence": "说明在哪条链路会意外加载整棵对象图。",
                    "bad_conclusion": "“所有一对多都不应该存在”。",
                    "repair": "建议用 projection 承接查询，并收紧关联语义。",
                },
                {
                    "name": "给老表加唯一约束",
                    "change": "MR 直接新增 unique key。",
                    "avoid": "不要只看 DDL 语法对不对，要看历史脏数据和上线窗口。",
                    "inspect": "查看历史重复数据处理、补数脚本和灰度兼容方案。",
                    "evidence": "证明线上会因脏数据或并发写入立刻失败。",
                    "bad_conclusion": "“加唯一约束能保证数据质量，所以一定是好事”。",
                    "repair": "建议先清洗数据、补冲突处理，再逐步收紧约束。",
                },
                {
                    "name": "事务里引入外部调用",
                    "change": "MR 在数据库写事务中加入远端请求。",
                    "avoid": "不要只从性能角度看，要看锁时间和一致性边界。",
                    "inspect": "查看事务范围、锁、重试和补偿逻辑。",
                    "evidence": "指出锁持有时间放大或提交/回滚顺序错位的风险。",
                    "bad_conclusion": "“请求很快，所以放事务里没关系”。",
                    "repair": "建议缩小事务并将外部副作用移到事务外或 outbox。",
                },
            ],
            final_checklist=[
                "是否明确指出了 schema、索引、事务或 ORM 哪一层出了问题？",
                "是否给出了足够具体的数据库证据，而不是笼统说“可能慢/可能锁表”？",
                "是否避免越界定义业务语义？",
                "是否说明了上线方式、兼容方式或回滚方式？",
                "是否覆盖了异步任务、报表、回放等非主链路？",
            ],
            glossary=[
                "在线演进: 在不中断服务的前提下逐步完成 schema 变更。",
                "锁风险: DDL/DML 对表、行或索引结构造成的等待与阻塞风险。",
                "N+1: 主查询后又为每条记录触发附加查询的 ORM 问题。",
                "读写链路一致性: 新 schema 被写入、读取、投影、回放同时正确理解。",
                "执行计划退化: SQL 因条件、排序或 join 变化导致成本陡增。",
            ],
        ),
    ]
)


def main() -> None:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    generated: list[tuple[str, int]] = []
    for profile in PROFILES:
        content = render_profile(profile)
        target = EXPORT_DIR / f"{profile['id']}.md"
        target.write_text(content, encoding="utf-8")
        generated.append((target.name, lines(content)))
    for name, count in generated:
        print(f"{name}\t{count}")


if __name__ == "__main__":
    main()
