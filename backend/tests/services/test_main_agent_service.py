from datetime import UTC, datetime

from app.domain.models.expert_profile import ExpertProfile
from app.domain.models.review import ReviewTask
from app.domain.models.review import ReviewSubject
from app.domain.models.runtime_settings import RuntimeSettings
from app.services.llm_chat_service import LLMTextResult
from app.services.main_agent_service import MainAgentService
from pathlib import Path


def test_main_agent_builds_related_file_chain_for_schedule_changes():
    agent = MainAgentService()
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="cal.com",
        project_id="calcom",
        source_ref="mr/28378",
        target_ref="main",
        changed_files=[
            "packages/prisma/migrations/001.sql",
            "packages/prisma/schema.prisma",
            "apps/api/schedules/output.service.ts",
            "packages/lib/schedules/getScheduleListItemData.ts",
        ],
    )

    chain = agent.build_change_chain(subject)

    assert "packages/lib/schedules/getScheduleListItemData.ts" in chain["related_files"]
    assert "packages/prisma/schema.prisma" in chain["related_files"]


def test_main_agent_change_chain_filters_test_files_for_business_flow():
    agent = MainAgentService()
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="mr/2",
        target_ref="main",
        changed_files=[
            "apps/api/orders/service.ts",
            "apps/api/orders/__tests__/service.test.ts",
            "packages/platform/types/order.output.ts",
        ],
    )

    chain = agent.build_change_chain(subject)

    assert "apps/api/orders/service.ts" in chain["related_files"]
    assert "packages/platform/types/order.output.ts" in chain["related_files"]
    assert all("__tests__" not in item for item in chain["related_files"])


def test_main_agent_command_exposes_disallowed_inference():
    agent = MainAgentService()
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="cal.com",
        project_id="calcom",
        source_ref="mr/28378",
        target_ref="main",
        changed_files=[
            "apps/api/schedules/output.service.ts",
            "packages/lib/schedules/getScheduleListItemData.ts",
        ],
    )
    expert = ExpertProfile(
        expert_id="correctness_business",
        name="Correctness",
        name_zh="正确性与业务专家",
        role="correctness",
        enabled=True,
        focus_areas=["业务规则"],
        activation_hints=["service", "transformer"],
        required_checks=["跨文件一致性"],
        out_of_scope=["不要凭 import 猜测未完成需求"],
        preferred_artifacts=["diff hunk", "调用链"],
        system_prompt="prompt",
    )

    command = agent.build_command(subject, expert, RuntimeSettings(allow_llm_fallback=True))

    assert "related_files" in command
    assert "disallowed_inference" in command
    assert "不要凭 import 猜测未完成需求" in command["disallowed_inference"]
    assert command["llm"]["mode"] == "template"
    assert "目标专家" in command["summary"]


def test_main_agent_builds_intake_summary_with_business_files():
    agent = MainAgentService()
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/x",
        target_ref="main",
        title="Order API update",
        mr_url="https://codehub.example/mr/1",
        changed_files=[
            "apps/api/orders/service.ts",
            "apps/api/orders/__tests__/service.test.ts",
            "packages/platform/types/order.output.ts",
        ],
        metadata={"platform_kind": "codehub", "compare_mode": "mr_compare", "remote_diff_fetched": True},
    )

    summary, metadata = agent.build_intake_summary(subject)

    assert "当前识别到 3 个变更文件" in summary
    assert metadata["platform_kind"] == "codehub"
    assert metadata["business_changed_files"] == [
        "apps/api/orders/service.ts",
        "packages/platform/types/order.output.ts",
    ]


def test_main_agent_does_not_fallback_to_runtime_file_without_changed_files():
    agent = MainAgentService()
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="mr/1",
        target_ref="main",
        changed_files=[],
        unified_diff="",
    )
    expert = ExpertProfile(
        expert_id="correctness_business",
        name="Correctness",
        name_zh="正确性与业务专家",
        role="correctness",
        enabled=True,
        focus_areas=["业务规则"],
        system_prompt="prompt",
    )

    command = agent.build_command(subject, expert, RuntimeSettings(allow_llm_fallback=True))

    assert command["file_path"] == ""
    assert command["routeable"] is False
    assert "未获取到真实 diff" in command["skip_reason"]


def test_main_agent_command_includes_repository_context_when_repo_is_ready(tmp_path: Path):
    repo_root = tmp_path / "repo"
    target = repo_root / "packages" / "lib" / "schedules" / "getScheduleListItemData.ts"
    target.parent.mkdir(parents=True)
    target.write_text("export const mapSchedule = (input) => input?.startTime ?? null\n", encoding="utf-8")

    agent = MainAgentService()
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="cal.com",
        project_id="calcom",
        source_ref="mr/28378",
        target_ref="main",
        changed_files=["packages/lib/schedules/getScheduleListItemData.ts"],
    )
    expert = ExpertProfile(
        expert_id="correctness_business",
        name="Correctness",
        name_zh="正确性与业务专家",
        role="correctness",
        enabled=True,
        focus_areas=["业务规则"],
        system_prompt="prompt",
    )

    command = agent.build_command(
        subject,
        expert,
        RuntimeSettings(
            allow_llm_fallback=True,
            code_repo_clone_url="https://github.com/example/repo.git",
            code_repo_local_path=str(repo_root),
            code_repo_default_branch="main",
        ),
    )

    assert command["repository_context"]["context_files"]
    assert "已补充" in command["repository_context"]["summary"]


def test_main_agent_command_includes_target_hunk_excerpt():
    agent = MainAgentService()
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/x",
        target_ref="main",
        changed_files=["src/service.ts"],
        unified_diff=(
            "diff --git a/src/service.ts b/src/service.ts\n"
            "--- a/src/service.ts\n"
            "+++ b/src/service.ts\n"
            "@@ -10,2 +10,3 @@ export const one = () => {\n"
            "   return 'a'\n"
            "+  console.log('a')\n"
            " }\n"
        ),
    )
    expert = ExpertProfile(
        expert_id="correctness_business",
        name="Correctness",
        name_zh="正确性与业务专家",
        role="correctness",
        enabled=True,
        focus_areas=["业务规则"],
        system_prompt="prompt",
    )

    command = agent.build_command(subject, expert, RuntimeSettings(allow_llm_fallback=True))

    assert command["target_hunk"]["hunk_header"]
    assert "console.log" in command["target_hunk"]["excerpt"]


def test_main_agent_prefers_security_hunk_for_security_expert():
    agent = MainAgentService()
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/x",
        target_ref="main",
        changed_files=["db/migrations/001.sql", "backend/app/security/authz.py"],
        unified_diff=(
            "diff --git a/db/migrations/001.sql b/db/migrations/001.sql\n"
            "--- a/db/migrations/001.sql\n"
            "+++ b/db/migrations/001.sql\n"
            "@@ -1,2 +1,3 @@\n"
            " CREATE TABLE users(id bigint);\n"
            "+CREATE INDEX idx_users_id ON users(id);\n"
            "diff --git a/backend/app/security/authz.py b/backend/app/security/authz.py\n"
            "--- a/backend/app/security/authz.py\n"
            "+++ b/backend/app/security/authz.py\n"
            "@@ -10,2 +10,4 @@ def can_access(user, resource):\n"
            "-    return True\n"
            "+    if not user.has_permission('admin'):\n"
            "+        raise PermissionError('forbidden')\n"
            "+    return True\n"
        ),
    )
    expert = ExpertProfile(
        expert_id="security_compliance",
        name="Security",
        name_zh="安全与合规专家",
        role="security",
        enabled=True,
        focus_areas=["鉴权授权"],
        activation_hints=["auth", "security"],
        system_prompt="prompt",
    )

    command = agent.build_command(subject, expert, RuntimeSettings(allow_llm_fallback=True))

    assert command["file_path"] == "backend/app/security/authz.py"
    assert "PermissionError" in command["target_hunk"]["excerpt"]


def test_main_agent_final_summary_marks_partial_failures_as_inconclusive():
    class StubLLM:
        def __init__(self) -> None:
            self.fallback_text = ""
            self.user_prompt = ""

        def resolve_main_agent(self, _runtime: RuntimeSettings):
            return None

        def complete_text(self, **kwargs):
            self.fallback_text = str(kwargs.get("fallback_text") or "")
            self.user_prompt = str(kwargs.get("user_prompt") or "")
            return LLMTextResult(
                text=self.fallback_text,
                mode="fallback",
                provider="stub",
                model="stub-model",
                base_url="https://example.invalid",
                api_key_env="DUMMY_API_KEY",
            )

    agent = MainAgentService()
    stub = StubLLM()
    agent._llm = stub  # type: ignore[assignment]
    review = ReviewTask(
        review_id="rev_partial",
        status="completed",
        phase="completed",
        analysis_mode="light",
        subject=ReviewSubject(
            subject_type="mr",
            repo_id="repo",
            project_id="proj",
            source_ref="feature/x",
            target_ref="main",
            changed_files=["src/main/java/com/example/OwnerController.java"],
        ),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    summary, _ = agent.build_final_summary(
        review,
        [],
        RuntimeSettings(allow_llm_fallback=True),
        partial_failure_count=1,
    )

    assert "部分完成" in summary
    assert "失败" in summary
    assert "专家执行失败数: 1" in stub.user_prompt


def test_main_agent_prefers_migration_hunk_for_database_expert():
    agent = MainAgentService()
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/x",
        target_ref="main",
        changed_files=["backend/app/services/order_service.py", "db/migrations/001.sql"],
        unified_diff=(
            "diff --git a/backend/app/services/order_service.py b/backend/app/services/order_service.py\n"
            "--- a/backend/app/services/order_service.py\n"
            "+++ b/backend/app/services/order_service.py\n"
            "@@ -10,2 +10,3 @@ def create_order():\n"
            "     return repo.save(order)\n"
            "+    logger.info('saved')\n"
            "diff --git a/db/migrations/001.sql b/db/migrations/001.sql\n"
            "--- a/db/migrations/001.sql\n"
            "+++ b/db/migrations/001.sql\n"
            "@@ -1,2 +1,4 @@\n"
            " CREATE TABLE orders(id bigint);\n"
            "+ALTER TABLE orders ADD COLUMN status varchar(32) NOT NULL DEFAULT 'new';\n"
            "+CREATE INDEX idx_orders_status ON orders(status);\n"
        ),
    )
    expert = ExpertProfile(
        expert_id="database_analysis",
        name="DB",
        name_zh="数据库分析专家",
        role="database",
        enabled=True,
        focus_areas=["SQL 与查询计划"],
        activation_hints=["sql", "migration", "schema", "repository"],
        system_prompt="prompt",
    )

    command = agent.build_command(subject, expert, RuntimeSettings(allow_llm_fallback=True))

    assert command["file_path"] == "db/migrations/001.sql"
    assert "ALTER TABLE orders" in command["target_hunk"]["excerpt"]


def test_main_agent_repository_context_includes_repo_search_hit_files(tmp_path: Path):
    repo_root = tmp_path / "repo"
    source = repo_root / "packages" / "lib" / "schedules" / "getScheduleListItemData.ts"
    related = repo_root / "apps" / "api" / "schedules" / "output.service.ts"
    source.parent.mkdir(parents=True)
    related.parent.mkdir(parents=True)
    source.write_text("export const getScheduleListItemData = () => updatedAt\n", encoding="utf-8")
    related.write_text(
        "import { getScheduleListItemData } from '@/packages/lib/schedules/getScheduleListItemData'\n"
        "export const mapOutput = () => getScheduleListItemData()\n",
        encoding="utf-8",
    )

    agent = MainAgentService()
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/x",
        target_ref="main",
        changed_files=["packages/lib/schedules/getScheduleListItemData.ts"],
        unified_diff=(
            "diff --git a/packages/lib/schedules/getScheduleListItemData.ts b/packages/lib/schedules/getScheduleListItemData.ts\n"
            "--- a/packages/lib/schedules/getScheduleListItemData.ts\n"
            "+++ b/packages/lib/schedules/getScheduleListItemData.ts\n"
            "@@ -1,2 +1,2 @@\n"
            "-export const getScheduleListItemData = () => null\n"
            "+export const getScheduleListItemData = () => updatedAt\n"
        ),
    )
    expert = ExpertProfile(
        expert_id="correctness_business",
        name="Correctness",
        name_zh="正确性与业务专家",
        role="correctness",
        enabled=True,
        focus_areas=["业务规则"],
        activation_hints=["service", "transformer"],
        system_prompt="prompt",
    )

    command = agent.build_command(
        subject,
        expert,
        RuntimeSettings(
            allow_llm_fallback=True,
            code_repo_clone_url="https://github.com/example/repo.git",
            code_repo_local_path=str(repo_root),
            code_repo_default_branch="main",
        ),
    )

    assert any(path.endswith("output.service.ts") for path in command["repository_context"]["context_files"])
    assert command["repository_context"]["search_matches"]
    assert command["repository_context"]["symbol_contexts"]


def test_main_agent_repository_context_filters_git_noise(tmp_path: Path):
    repo_root = tmp_path / "repo"
    source = repo_root / "packages" / "lib" / "schedules" / "getScheduleListItemData.ts"
    git_index = repo_root / ".git" / "index"
    source.parent.mkdir(parents=True)
    git_index.parent.mkdir(parents=True)
    source.write_text("export const getScheduleListItemData = () => updatedAt\n", encoding="utf-8")
    git_index.write_text("getScheduleListItemData\n", encoding="utf-8")

    agent = MainAgentService()
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/x",
        target_ref="main",
        changed_files=["packages/lib/schedules/getScheduleListItemData.ts"],
        unified_diff=(
            "diff --git a/packages/lib/schedules/getScheduleListItemData.ts b/packages/lib/schedules/getScheduleListItemData.ts\n"
            "--- a/packages/lib/schedules/getScheduleListItemData.ts\n"
            "+++ b/packages/lib/schedules/getScheduleListItemData.ts\n"
            "@@ -1,2 +1,2 @@\n"
            "-export const getScheduleListItemData = () => null\n"
            "+export const getScheduleListItemData = () => updatedAt\n"
        ),
    )
    expert = ExpertProfile(
        expert_id="correctness_business",
        name="Correctness",
        name_zh="正确性与业务专家",
        role="correctness",
        enabled=True,
        focus_areas=["业务规则"],
        activation_hints=["service", "transformer"],
        system_prompt="prompt",
    )

    command = agent.build_command(
        subject,
        expert,
        RuntimeSettings(
            allow_llm_fallback=True,
            code_repo_clone_url="https://github.com/example/repo.git",
            code_repo_local_path=str(repo_root),
            code_repo_default_branch="main",
        ),
    )

    assert ".git/index" not in command["repository_context"]["context_files"]


def test_main_agent_correctness_prefers_transformer_chain_for_timestamp_changes():
    agent = MainAgentService()
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="cal.com",
        project_id="calcom",
        source_ref="mr/28378",
        target_ref="main",
        changed_files=[
            "apps/api/schedules/output.service.ts",
            "packages/platform/types/schedule.output.ts",
            "packages/prisma/schema.prisma",
            "packages/lib/schedules/transformers/getScheduleListItemData.ts",
        ],
        unified_diff=(
            "diff --git a/apps/api/schedules/output.service.ts b/apps/api/schedules/output.service.ts\n"
            "--- a/apps/api/schedules/output.service.ts\n"
            "+++ b/apps/api/schedules/output.service.ts\n"
            "@@ -10,2 +10,4 @@\n"
            "+import { UsersRepository } from '@/modules/users/users.repository';\n"
            "diff --git a/packages/lib/schedules/transformers/getScheduleListItemData.ts b/packages/lib/schedules/transformers/getScheduleListItemData.ts\n"
            "--- a/packages/lib/schedules/transformers/getScheduleListItemData.ts\n"
            "+++ b/packages/lib/schedules/transformers/getScheduleListItemData.ts\n"
            "@@ -12,2 +12,4 @@ export type Schedule = {\n"
            "+    createdAt: Date | null;\n"
            "+    updatedAt: Date | null;\n"
        ),
    )
    expert = ExpertProfile(
        expert_id="correctness_business",
        name="Correctness",
        name_zh="正确性与业务专家",
        role="correctness",
        enabled=True,
        focus_areas=["业务规则"],
        activation_hints=["service", "transformer"],
        system_prompt="prompt",
    )

    command = agent.build_command(subject, expert, RuntimeSettings(allow_llm_fallback=True))

    assert command["file_path"] == "packages/lib/schedules/transformers/getScheduleListItemData.ts"
    assert "apps/api/schedules/output.service.ts" in command["related_files"]
    assert "packages/platform/types/schedule.output.ts" in command["related_files"]


def test_main_agent_skips_mq_expert_on_non_mq_change():
    agent = MainAgentService()
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/x",
        target_ref="main",
        changed_files=["apps/api/schedules/output.service.ts"],
        unified_diff=(
            "diff --git a/apps/api/schedules/output.service.ts b/apps/api/schedules/output.service.ts\n"
            "--- a/apps/api/schedules/output.service.ts\n"
            "+++ b/apps/api/schedules/output.service.ts\n"
            "@@ -1,2 +1,3 @@\n"
            "+import { Injectable } from '@nestjs/common'\n"
        ),
    )
    expert = ExpertProfile(
        expert_id="mq_analysis",
        name="MQ",
        name_zh="MQ分析专家",
        role="mq",
        enabled=True,
        focus_areas=["幂等与去重"],
        activation_hints=["mq", "queue", "consumer"],
        system_prompt="prompt",
    )

    command = agent.build_command(subject, expert, RuntimeSettings(allow_llm_fallback=True))

    assert command["routeable"] is False
    assert "未命中" in command["skip_reason"]


def test_main_agent_does_not_route_security_from_patch_mail_headers():
    agent = MainAgentService()
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="cal.com",
        project_id="calcom",
        source_ref="mr/28378",
        target_ref="main",
        changed_files=["packages/prisma/schema.prisma"],
        unified_diff=(
            "diff --git a/packages/prisma/schema.prisma b/packages/prisma/schema.prisma\n"
            "--- a/packages/prisma/schema.prisma\n"
            "+++ b/packages/prisma/schema.prisma\n"
            "@@ -994,6 +994,8 @@ model Availability {\n"
            "   date        DateTime?  @db.Date\n"
            "   Schedule    Schedule?  @relation(fields: [scheduleId], references: [id])\n"
            "   scheduleId  Int?\n"
            "+  createdAt   DateTime?  @default(now())\n"
            "+  updatedAt   DateTime?  @updatedAt\n"
            " \n"
            "   @@index([userId])\n"
            "From c898b98e6f17d873ef6ec1c291ec6618b8360e36 Mon Sep 17 00:00:00 2001\n"
            "Co-Authored-By: joe@cal.com <j.auyeung419@gmail.com>\n"
        ),
    )
    expert = ExpertProfile(
        expert_id="security_compliance",
        name="Security",
        name_zh="安全与合规专家",
        role="security",
        enabled=True,
        focus_areas=["鉴权授权"],
        activation_hints=["auth", "security"],
        system_prompt="prompt",
    )

    command = agent.build_command(subject, expert, RuntimeSettings(allow_llm_fallback=True))

    assert command["routeable"] is False
    assert "安全" in command["skip_reason"]


def test_main_agent_routes_security_for_java_decoder_memory_change():
    agent = MainAgentService()
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/x",
        target_ref="main",
        changed_files=[
            "sentinel-cluster/sentinel-cluster-server-default/src/main/java/com/alibaba/csp/sentinel/cluster/server/codec/data/ParamFlowRequestDataDecoder.java"
        ],
        unified_diff=(
            "diff --git a/sentinel-cluster/sentinel-cluster-server-default/src/main/java/com/alibaba/csp/sentinel/cluster/server/codec/data/ParamFlowRequestDataDecoder.java "
            "b/sentinel-cluster/sentinel-cluster-server-default/src/main/java/com/alibaba/csp/sentinel/cluster/server/codec/data/ParamFlowRequestDataDecoder.java\n"
            "--- a/sentinel-cluster/sentinel-cluster-server-default/src/main/java/com/alibaba/csp/sentinel/cluster/server/codec/data/ParamFlowRequestDataDecoder.java\n"
            "+++ b/sentinel-cluster/sentinel-cluster-server-default/src/main/java/com/alibaba/csp/sentinel/cluster/server/codec/data/ParamFlowRequestDataDecoder.java\n"
            "@@ -10,2 +10,5 @@ public class ParamFlowRequestDataDecoder {\n"
            "+    if (frameLength > ServerConstants.NETTY_MAX_FRAME_LENGTH) {\n"
            "+        throw new IllegalArgumentException(\"prevent memory issues\");\n"
            "+    }\n"
        ),
    )
    expert = ExpertProfile(
        expert_id="security_compliance",
        name="Security",
        name_zh="安全与合规专家",
        role="security",
        enabled=True,
        focus_areas=["鉴权授权"],
        activation_hints=["security", "decoder", "memory"],
        system_prompt="prompt",
    )

    command = agent.build_command(subject, expert, RuntimeSettings(allow_llm_fallback=True))

    assert command["routeable"] is True
    assert "frameLength" in command["target_hunk"]["excerpt"]


def test_main_agent_routes_security_when_java_memory_signal_is_in_related_hunk():
    agent = MainAgentService()
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/x",
        target_ref="main",
        changed_files=[
            "sentinel-cluster/sentinel-cluster-server-default/src/main/java/com/alibaba/csp/sentinel/cluster/server/NettyTransportServer.java",
            "sentinel-cluster/sentinel-cluster-server-default/src/main/java/com/alibaba/csp/sentinel/cluster/server/codec/data/ParamFlowRequestDataDecoder.java",
        ],
        unified_diff=(
            "diff --git a/sentinel-cluster/sentinel-cluster-server-default/src/main/java/com/alibaba/csp/sentinel/cluster/server/NettyTransportServer.java "
            "b/sentinel-cluster/sentinel-cluster-server-default/src/main/java/com/alibaba/csp/sentinel/cluster/server/NettyTransportServer.java\n"
            "--- a/sentinel-cluster/sentinel-cluster-server-default/src/main/java/com/alibaba/csp/sentinel/cluster/server/NettyTransportServer.java\n"
            "+++ b/sentinel-cluster/sentinel-cluster-server-default/src/main/java/com/alibaba/csp/sentinel/cluster/server/NettyTransportServer.java\n"
            "@@ -1,2 +1,2 @@\n"
            "-serverBootstrap.childHandler(childChannelHandler);\n"
            "+serverBootstrap.childHandler(childChannelHandler).option(ChannelOption.SO_BACKLOG, 1024);\n"
            "diff --git a/sentinel-cluster/sentinel-cluster-server-default/src/main/java/com/alibaba/csp/sentinel/cluster/server/codec/data/ParamFlowRequestDataDecoder.java "
            "b/sentinel-cluster/sentinel-cluster-server-default/src/main/java/com/alibaba/csp/sentinel/cluster/server/codec/data/ParamFlowRequestDataDecoder.java\n"
            "--- a/sentinel-cluster/sentinel-cluster-server-default/src/main/java/com/alibaba/csp/sentinel/cluster/server/codec/data/ParamFlowRequestDataDecoder.java\n"
            "+++ b/sentinel-cluster/sentinel-cluster-server-default/src/main/java/com/alibaba/csp/sentinel/cluster/server/codec/data/ParamFlowRequestDataDecoder.java\n"
            "@@ -10,2 +10,5 @@ public class ParamFlowRequestDataDecoder {\n"
            "+    if (frameLength > ServerConstants.NETTY_MAX_FRAME_LENGTH) {\n"
            "+        throw new IllegalArgumentException(\"prevent memory issues\");\n"
            "+    }\n"
        ),
    )
    expert = ExpertProfile(
        expert_id="security_compliance",
        name="Security",
        name_zh="安全与合规专家",
        role="security",
        enabled=True,
        focus_areas=["鉴权授权"],
        activation_hints=["security", "decoder", "memory"],
        system_prompt="prompt",
    )

    command = agent.build_command(subject, expert, RuntimeSettings(allow_llm_fallback=True))

    assert command["routeable"] is True


def test_main_agent_does_not_route_security_from_author_comment_noise():
    agent = MainAgentService()
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/x",
        target_ref="main",
        changed_files=["server/NettyTransportServer.java"],
        unified_diff=(
            "diff --git a/server/NettyTransportServer.java b/server/NettyTransportServer.java\n"
            "--- a/server/NettyTransportServer.java\n"
            "+++ b/server/NettyTransportServer.java\n"
            "@@ -1,5 +1,5 @@\n"
            " /**\n"
            "  * @author Eric Zhao\n"
            "  */\n"
            "-import static server.ServerConstants.*;\n"
            "+import static server.ServerConstants.NETTY_MAX_FRAME_LENGTH;\n"
        ),
    )
    expert = ExpertProfile(
        expert_id="security_compliance",
        name="Security",
        name_zh="安全与合规专家",
        role="security",
        enabled=True,
        focus_areas=["鉴权授权"],
        activation_hints=["auth", "security"],
        system_prompt="prompt",
    )

    command = agent.build_command(subject, expert, RuntimeSettings(allow_llm_fallback=True))

    assert command["routeable"] is False
    assert "import" in command["skip_reason"]


def test_main_agent_skips_architecture_on_import_only_hunk():
    agent = MainAgentService()
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/x",
        target_ref="main",
        changed_files=["apps/api/schedules/output.service.ts"],
        unified_diff=(
            "diff --git a/apps/api/schedules/output.service.ts b/apps/api/schedules/output.service.ts\n"
            "--- a/apps/api/schedules/output.service.ts\n"
            "+++ b/apps/api/schedules/output.service.ts\n"
            "@@ -1,2 +1,3 @@\n"
            "-import { UsersRepository } from '@/modules/users/users.repository'\n"
            "+import { UsersRepository } from '@/modules/users/users.repository'\n"
            "+import { Injectable } from '@nestjs/common'\n"
        ),
    )
    expert = ExpertProfile(
        expert_id="architecture_design",
        name="Architecture",
        name_zh="架构与设计专家",
        role="architecture",
        enabled=True,
        focus_areas=["模块边界"],
        activation_hints=["service", "module", "api"],
        system_prompt="prompt",
    )

    command = agent.build_command(subject, expert, RuntimeSettings(allow_llm_fallback=True))

    assert command["routeable"] is False
    assert "import" in command["skip_reason"]


def test_main_agent_treats_import_change_with_context_lines_as_import_only():
    agent = MainAgentService()
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/x",
        target_ref="main",
        changed_files=["apps/api/schedules/output.service.ts"],
        unified_diff=(
            "diff --git a/apps/api/schedules/output.service.ts b/apps/api/schedules/output.service.ts\n"
            "--- a/apps/api/schedules/output.service.ts\n"
            "+++ b/apps/api/schedules/output.service.ts\n"
            "@@ -1,8 +1,7 @@\n"
            "-import { UsersRepository } from '@/modules/users/users.repository';\n"
            "-import { Injectable } from '@nestjs/common';\n"
            "-\n"
            " import type { WeekDay } from '@calcom/platform-types';\n"
            " import type { Availability, Schedule } from '@calcom/prisma/client';\n"
            "+import { Injectable } from '@nestjs/common';\n"
            "+import { UsersRepository } from '@/modules/users/users.repository';\n"
            " \n"
            " type DatabaseSchedule = Schedule & { availability: Availability[] };\n"
        ),
    )
    expert = ExpertProfile(
        expert_id="maintainability_code_health",
        name="Maintainability",
        name_zh="可维护性与代码健康专家",
        role="maintainability",
        enabled=True,
        focus_areas=["复杂度"],
        activation_hints=["service", "module", "api"],
        system_prompt="prompt",
    )

    command = agent.build_command(subject, expert, RuntimeSettings(allow_llm_fallback=True))

    assert command["routeable"] is False
    assert "import" in command["skip_reason"]


def test_main_agent_prefers_non_import_hunk_when_same_file_has_real_behavior_change():
    agent = MainAgentService()
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/x",
        target_ref="main",
        changed_files=["src/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java"],
        unified_diff=(
            "diff --git a/src/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java b/src/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java\n"
            "--- a/src/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java\n"
            "+++ b/src/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java\n"
            "@@ -6,3 +6,3 @@\n"
            "-import tv.codely.shared.infrastructure.bus.event.spring.SpringApplicationEventBus;\n"
            "+import tv.codely.shared.domain.bus.event.EventBus;\n"
            "@@ -20,3 +20,3 @@ public class MySqlDomainEventsConsumer {\n"
            "-    private final SpringApplicationEventBus bus;\n"
            "+    private final EventBus bus;\n"
            "@@ -30,2 +30,2 @@\n"
            "-        SpringApplicationEventBus bus\n"
            "+        EventBus bus\n"
        ),
    )
    expert = ExpertProfile(
        expert_id="architecture_design",
        name="Architecture",
        name_zh="架构与设计专家",
        role="architecture",
        enabled=True,
        focus_areas=["模块边界"],
        activation_hints=["event", "bus", "domain", "application"],
        system_prompt="prompt",
    )

    command = agent.build_command(subject, expert, RuntimeSettings(allow_llm_fallback=True))

    assert command["routeable"] is True
    assert "private final EventBus bus" in command["target_hunk"]["excerpt"]


def test_main_agent_prefers_semantic_change_over_format_only_sql_hunk():
    agent = MainAgentService()
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/x",
        target_ref="main",
        changed_files=[
            "src/mooc/main/resources/database/mooc.sql",
            "src/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java",
        ],
        unified_diff=(
            "diff --git a/src/mooc/main/resources/database/mooc.sql b/src/mooc/main/resources/database/mooc.sql\n"
            "--- a/src/mooc/main/resources/database/mooc.sql\n"
            "+++ b/src/mooc/main/resources/database/mooc.sql\n"
            "@@ -1,4 +1,4 @@\n"
            "-    id       CHAR(36)     NOT NULL,\n"
            "+\tid CHAR(36) NOT NULL,\n"
            "-    name     VARCHAR(255) NOT NULL,\n"
            "+\tname VARCHAR(255) NOT NULL,\n"
            "diff --git a/src/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java b/src/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java\n"
            "--- a/src/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java\n"
            "+++ b/src/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java\n"
            "@@ -20,3 +20,3 @@ public class MySqlDomainEventsConsumer {\n"
            "-    private final SpringApplicationEventBus bus;\n"
            "+    private final EventBus bus;\n"
        ),
    )
    expert = ExpertProfile(
        expert_id="correctness_business",
        name="Correctness",
        name_zh="正确性与业务专家",
        role="correctness",
        enabled=True,
        focus_areas=["业务规则"],
        activation_hints=["event", "bus", "domain"],
        system_prompt="prompt",
    )

    command = agent.build_command(subject, expert, RuntimeSettings(allow_llm_fallback=True))

    assert command["file_path"] == "src/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java"
    assert "EventBus" in command["target_hunk"]["excerpt"]


def test_main_agent_candidate_hunks_drop_import_only_when_same_file_has_substantive_hunk():
    agent = MainAgentService()
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/x",
        target_ref="main",
        changed_files=["src/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java"],
        unified_diff=(
            "diff --git a/src/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java b/src/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java\n"
            "--- a/src/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java\n"
            "+++ b/src/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java\n"
            "@@ -6,3 +6,3 @@\n"
            "-import tv.codely.shared.infrastructure.bus.event.spring.SpringApplicationEventBus;\n"
            "+import tv.codely.shared.domain.bus.event.EventBus;\n"
            "@@ -20,3 +20,3 @@ public class MySqlDomainEventsConsumer {\n"
            "-    private final SpringApplicationEventBus bus;\n"
            "+    private final EventBus bus;\n"
        ),
    )

    candidates = agent._build_candidate_hunks(subject, agent._build_repository_service(RuntimeSettings()))

    assert len(candidates) == 1
    assert "private final EventBus bus" in candidates[0]["excerpt"]


def test_main_agent_candidate_hunks_drop_format_only_file_when_review_has_substantive_change():
    agent = MainAgentService()
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/x",
        target_ref="main",
        changed_files=[
            "src/mooc/main/resources/database/mooc.sql",
            "src/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java",
        ],
        unified_diff=(
            "diff --git a/src/mooc/main/resources/database/mooc.sql b/src/mooc/main/resources/database/mooc.sql\n"
            "--- a/src/mooc/main/resources/database/mooc.sql\n"
            "+++ b/src/mooc/main/resources/database/mooc.sql\n"
            "@@ -1,4 +1,4 @@\n"
            "-    id       CHAR(36)     NOT NULL,\n"
            "+\tid CHAR(36) NOT NULL,\n"
            "-    name     VARCHAR(255) NOT NULL,\n"
            "+\tname VARCHAR(255) NOT NULL,\n"
            "diff --git a/src/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java b/src/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java\n"
            "--- a/src/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java\n"
            "+++ b/src/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java\n"
            "@@ -20,3 +20,3 @@ public class MySqlDomainEventsConsumer {\n"
            "-    private final SpringApplicationEventBus bus;\n"
            "+    private final EventBus bus;\n"
        ),
    )

    candidates = agent._build_candidate_hunks(subject, agent._build_repository_service(RuntimeSettings()))

    assert len(candidates) == 1
    assert candidates[0]["file_path"] == "src/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java"
    assert "EventBus" in candidates[0]["excerpt"]


def test_main_agent_candidate_hunks_preserve_real_changed_lines():
    agent = MainAgentService()
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/x",
        target_ref="main",
        changed_files=["src/mooc/main/tv/codely/mooc/courses/application/create/CourseCreator.java"],
        unified_diff=(
            "diff --git a/src/mooc/main/tv/codely/mooc/courses/application/create/CourseCreator.java b/src/mooc/main/tv/codely/mooc/courses/application/create/CourseCreator.java\n"
            "--- a/src/mooc/main/tv/codely/mooc/courses/application/create/CourseCreator.java\n"
            "+++ b/src/mooc/main/tv/codely/mooc/courses/application/create/CourseCreator.java\n"
            "@@ -15,9 +15,9 @@ public final class CourseCreator {\n"
            "     }\n"
            " \n"
            "     public void create(CourseId id, CourseName name, CourseDuration duration) {\n"
            "-        Course course = Course.create(id, name, duration);\n"
            "+        Course course = new Course(id, name, duration);\n"
            " \n"
            "-        repository.save(course);\n"
            "         eventBus.publish(course.pullDomainEvents());\n"
            "+        repository.save(course);\n"
            "     }\n"
            " }\n"
        ),
    )

    candidates = agent._build_candidate_hunks(subject, agent._build_repository_service(RuntimeSettings()))

    assert len(candidates) == 1
    assert candidates[0]["start_line"] == 15
    assert candidates[0]["changed_lines"] == [18, 21]


def test_main_agent_treats_sql_line_wrap_reflow_as_format_only():
    agent = MainAgentService()
    excerpt = (
        "# src/mooc/main/resources/database/mooc.sql\n"
        "   - | INSERT IGNORE INTO courses_counter (id, total, existing_courses) VALUES ('id', 0, '[]');\n"
        "   1 | +INSERT IGNORE INTO courses_counter (id, total, existing_courses)\n"
        "   2 | +VALUES ('id', 0, '[]');\n"
    )

    assert agent._is_format_only_hunk(excerpt) is True


def test_main_agent_build_routing_plan_uses_llm_routes(monkeypatch):
    agent = MainAgentService()
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/design",
        target_ref="main",
        changed_files=[
            "apps/api/orders/order.service.ts",
            "packages/platform/types/order.output.ts",
        ],
        unified_diff=(
            "diff --git a/apps/api/orders/order.service.ts b/apps/api/orders/order.service.ts\n"
            "--- a/apps/api/orders/order.service.ts\n"
            "+++ b/apps/api/orders/order.service.ts\n"
            "@@ -10,3 +10,5 @@ export async function createOrder(payload) {\n"
            "+  return mapOrderOutput(payload)\n"
            " }\n"
            "diff --git a/packages/platform/types/order.output.ts b/packages/platform/types/order.output.ts\n"
            "--- a/packages/platform/types/order.output.ts\n"
            "+++ b/packages/platform/types/order.output.ts\n"
            "@@ -1,2 +1,4 @@ export type OrderOutput = {\n"
            "+  createdAt?: string;\n"
            "+  updatedAt?: string;\n"
            " }\n"
        ),
    )
    experts = [
        ExpertProfile(
            expert_id="correctness_business",
            name="Correctness",
            name_zh="正确性与业务专家",
            role="correctness",
            enabled=True,
            focus_areas=["业务正确性"],
            activation_hints=["output", "service", "transformer"],
            required_checks=["跨文件一致性"],
            system_prompt="prompt",
        ),
        ExpertProfile(
            expert_id="architecture_design",
            name="Architecture",
            name_zh="架构与设计专家",
            role="architecture",
            enabled=True,
            focus_areas=["模块边界"],
            activation_hints=["module", "service"],
            system_prompt="prompt",
        ),
    ]

    def fake_complete_text(**_: object) -> LLMTextResult:
        return LLMTextResult(
            text=(
                '{"expert_routes":['
                '{"expert_id":"correctness_business","candidate_id":"packages/platform/types/order.output.ts:1:1","routeable":true,"reason":"字段契约变化更适合正确性专家","confidence":0.94},'
                '{"expert_id":"architecture_design","candidate_id":"apps/api/orders/order.service.ts:10:1","routeable":true,"reason":"服务层接口编排适合架构专家","confidence":0.79}'
                '],"skipped_experts":[]}'
            ),
            mode="live",
            provider="test",
            model="test",
            base_url="http://llm.test",
            api_key_env="TEST_KEY",
        )

    monkeypatch.setattr(agent._llm, "complete_text", fake_complete_text)

    plan = agent.build_routing_plan(subject, experts, RuntimeSettings(allow_llm_fallback=True))

    assert plan["correctness_business"]["routing_source"] == "llm"
    assert plan["correctness_business"]["file_path"] == "packages/platform/types/order.output.ts"
    assert "字段契约变化" in plan["correctness_business"]["routing_reason"]
    assert plan["architecture_design"]["file_path"] == "apps/api/orders/order.service.ts"


def test_main_agent_light_routing_plan_preserves_selected_security_expert():
    agent = MainAgentService()
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/composite",
        target_ref="main",
        changed_files=[
            "src/shared/main/tv/codely/shared/infrastructure/hibernate/HibernateCriteriaConverter.java",
        ],
        unified_diff=(
            "diff --git a/src/shared/main/tv/codely/shared/infrastructure/hibernate/HibernateCriteriaConverter.java "
            "b/src/shared/main/tv/codely/shared/infrastructure/hibernate/HibernateCriteriaConverter.java\n"
            "--- a/src/shared/main/tv/codely/shared/infrastructure/hibernate/HibernateCriteriaConverter.java\n"
            "+++ b/src/shared/main/tv/codely/shared/infrastructure/hibernate/HibernateCriteriaConverter.java\n"
            "@@ -60,7 +60,7 @@ public final class HibernateCriteriaConverter<T> {\n"
            '-        return builder.equal(root.get(filter.field().value()), filter.value().value());\n'
            '+        return builder.like(root.get(filter.field().value()), String.format("%%%s%%", filter.value().value()));\n'
        ),
    )
    experts = [
        ExpertProfile(
            expert_id="security_compliance",
            name="Security",
            name_zh="安全与合规专家",
            role="security",
            enabled=True,
            focus_areas=["鉴权授权", "输入校验"],
            activation_hints=["auth", "security", "validation"],
            system_prompt="prompt",
        )
    ]

    plan = agent.build_routing_plan(subject, experts, RuntimeSettings(), analysis_mode="light")

    route = plan["security_compliance"]
    assert route["routeable"] is True
    assert route["routing_source"] == "selected_override"
    assert "保守执行策略继续审查" in route["routing_reason"]
    assert route["routing_override_reason"] == "当前变更未命中安全相关线索"


def test_main_agent_routing_plan_overrides_llm_skip_for_selected_expert(monkeypatch):
    agent = MainAgentService()
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/composite",
        target_ref="main",
        changed_files=[
            "src/shared/main/tv/codely/shared/infrastructure/hibernate/HibernateCriteriaConverter.java",
        ],
        unified_diff=(
            "diff --git a/src/shared/main/tv/codely/shared/infrastructure/hibernate/HibernateCriteriaConverter.java "
            "b/src/shared/main/tv/codely/shared/infrastructure/hibernate/HibernateCriteriaConverter.java\n"
            "--- a/src/shared/main/tv/codely/shared/infrastructure/hibernate/HibernateCriteriaConverter.java\n"
            "+++ b/src/shared/main/tv/codely/shared/infrastructure/hibernate/HibernateCriteriaConverter.java\n"
            "@@ -60,7 +60,7 @@ public final class HibernateCriteriaConverter<T> {\n"
            '-        return builder.equal(root.get(filter.field().value()), filter.value().value());\n'
            '+        return builder.like(root.get(filter.field().value()), String.format("%%%s%%", filter.value().value()));\n'
        ),
    )
    experts = [
        ExpertProfile(
            expert_id="security_compliance",
            name="Security",
            name_zh="安全与合规专家",
            role="security",
            enabled=True,
            focus_areas=["鉴权授权", "输入校验"],
            activation_hints=["auth", "security", "validation"],
            system_prompt="prompt",
        )
    ]

    def fake_complete_text(**_: object) -> LLMTextResult:
        return LLMTextResult(
            text=(
                '{"expert_routes":[],'
                '"skipped_experts":['
                '{"expert_id":"security_compliance","reason":"当前变更未命中安全相关线索"}'
                "]}",
            ),
            mode="live",
            provider="test",
            model="test",
            base_url="http://llm.test",
            api_key_env="TEST_KEY",
        )

    monkeypatch.setattr(agent._llm, "complete_text", fake_complete_text)

    plan = agent.build_routing_plan(subject, experts, RuntimeSettings(allow_llm_fallback=True))

    route = plan["security_compliance"]
    assert route["routeable"] is True
    assert route["routing_source"] == "selected_override"
    assert route["routing_override_reason"] == "当前变更未命中安全相关线索"


def test_main_agent_build_command_respects_route_hint_routeable_override():
    agent = MainAgentService()
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/composite",
        target_ref="main",
        changed_files=[
            "src/shared/main/tv/codely/shared/infrastructure/hibernate/HibernateCriteriaConverter.java",
        ],
        unified_diff=(
            "diff --git a/src/shared/main/tv/codely/shared/infrastructure/hibernate/HibernateCriteriaConverter.java "
            "b/src/shared/main/tv/codely/shared/infrastructure/hibernate/HibernateCriteriaConverter.java\n"
            "--- a/src/shared/main/tv/codely/shared/infrastructure/hibernate/HibernateCriteriaConverter.java\n"
            "+++ b/src/shared/main/tv/codely/shared/infrastructure/hibernate/HibernateCriteriaConverter.java\n"
            "@@ -60,7 +60,7 @@ public final class HibernateCriteriaConverter<T> {\n"
            '-        return builder.equal(root.get(filter.field().value()), filter.value().value());\n'
            '+        return builder.like(root.get(filter.field().value()), String.format("%%%s%%", filter.value().value()));\n'
        ),
    )
    expert = ExpertProfile(
        expert_id="security_compliance",
        name="Security",
        name_zh="安全与合规专家",
        role="security",
        enabled=True,
        focus_areas=["鉴权授权", "输入校验"],
        activation_hints=["auth", "security", "validation"],
        system_prompt="prompt",
    )

    command = agent.build_command(
        subject,
        expert,
        RuntimeSettings(),
        route_hint={
            "file_path": "src/shared/main/tv/codely/shared/infrastructure/hibernate/HibernateCriteriaConverter.java",
            "line_start": 63,
            "target_hunk": {"excerpt": '+        return builder.like(root.get(filter.field().value()), String.format("%%%s%%", filter.value().value()));\n'},
            "repo_hits": {},
            "routeable": True,
            "skip_reason": "",
            "confidence": 0.31,
            "routing_reason": "主Agent已选中该专家，本轮按保守执行策略继续审查。",
        },
    )

    assert command["routeable"] is True
    assert command["skip_reason"] == ""


def test_main_agent_select_review_experts_uses_llm_result(monkeypatch):
    agent = MainAgentService()
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/design",
        target_ref="main",
        changed_files=[
            "apps/api/orders/order.service.ts",
            "packages/platform/types/order.output.ts",
        ],
        unified_diff=(
            "diff --git a/apps/api/orders/order.service.ts b/apps/api/orders/order.service.ts\n"
            "--- a/apps/api/orders/order.service.ts\n"
            "+++ b/apps/api/orders/order.service.ts\n"
            "@@ -10,3 +10,5 @@ export async function createOrder(payload) {\n"
            "+  return mapOrderOutput(payload)\n"
            " }\n"
        ),
    )
    experts = [
        ExpertProfile(
            expert_id="correctness_business",
            name="Correctness",
            name_zh="正确性与业务专家",
            role="correctness",
            enabled=True,
            focus_areas=["业务正确性"],
            system_prompt="prompt",
        ),
        ExpertProfile(
            expert_id="architecture_design",
            name="Architecture",
            name_zh="架构与设计专家",
            role="architecture",
            enabled=True,
            focus_areas=["模块边界"],
            system_prompt="prompt",
        ),
        ExpertProfile(
            expert_id="security_compliance",
            name="Security",
            name_zh="安全与合规专家",
            role="security",
            enabled=True,
            focus_areas=["安全边界"],
            system_prompt="prompt",
        ),
    ]

    def fake_complete_text(**_: object) -> LLMTextResult:
        return LLMTextResult(
            text=(
                '{"selected_experts":['
                '{"expert_id":"correctness_business","reason":"字段契约与业务语义变更明显","confidence":0.95},'
                '{"expert_id":"architecture_design","reason":"服务层编排发生变化","confidence":0.82}'
                '],"skipped_experts":['
                '{"expert_id":"security_compliance","reason":"当前 diff 未出现安全相关信号"}'
                ']}'
            ),
            mode="live",
            provider="test",
            model="test",
            base_url="http://llm.test",
            api_key_env="TEST_KEY",
        )

    monkeypatch.setattr(agent._llm, "complete_text", fake_complete_text)

    plan = agent.select_review_experts(
        subject,
        experts,
        RuntimeSettings(allow_llm_fallback=True),
        requested_expert_ids=["correctness_business", "security_compliance"],
    )

    assert plan["selected_expert_ids"] == ["correctness_business", "architecture_design"]
    assert plan["selected_experts"][0]["reason"] == "字段契约与业务语义变更明显"
    assert plan["skipped_experts"][0]["expert_id"] == "security_compliance"


def test_main_agent_uses_runtime_timeout_for_expert_selection(monkeypatch):
    agent = MainAgentService()
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/design",
        target_ref="main",
        changed_files=["apps/api/orders/order.service.ts"],
        unified_diff="diff --git a/apps/api/orders/order.service.ts b/apps/api/orders/order.service.ts\n",
    )
    experts = [
        ExpertProfile(
            expert_id="correctness_business",
            name="Correctness",
            name_zh="正确性与业务专家",
            role="correctness",
            enabled=True,
            focus_areas=["业务正确性"],
            system_prompt="prompt",
        )
    ]
    captured: dict[str, object] = {}

    def fake_complete_text(**kwargs: object) -> LLMTextResult:
        captured["timeout_seconds"] = kwargs.get("timeout_seconds")
        captured["max_attempts"] = kwargs.get("max_attempts")
        return LLMTextResult(
            text='{"selected_experts":[{"expert_id":"correctness_business","reason":"ok","confidence":0.9}],"skipped_experts":[]}',
            mode="live",
            provider="test",
            model="test",
            base_url="http://llm.test",
            api_key_env="TEST_KEY",
        )

    monkeypatch.setattr(agent._llm, "complete_text", fake_complete_text)

    runtime = RuntimeSettings(
        allow_llm_fallback=True,
        standard_llm_timeout_seconds=150,
        standard_llm_retry_count=4,
        light_llm_timeout_seconds=220,
        light_llm_retry_count=2,
    )
    plan = agent.select_review_experts(subject, experts, runtime, requested_expert_ids=["correctness_business"])

    assert plan["selected_expert_ids"] == ["correctness_business"]
    assert captured["timeout_seconds"] == 220.0
    assert captured["max_attempts"] == 4


def test_main_agent_uses_runtime_timeout_for_routing_plan(monkeypatch):
    agent = MainAgentService()
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/design",
        target_ref="main",
        changed_files=["apps/api/orders/order.service.ts"],
        unified_diff=(
            "diff --git a/apps/api/orders/order.service.ts b/apps/api/orders/order.service.ts\n"
            "--- a/apps/api/orders/order.service.ts\n"
            "+++ b/apps/api/orders/order.service.ts\n"
            "@@ -1,2 +1,2 @@\n"
            "+ return payload\n"
        ),
    )
    experts = [
        ExpertProfile(
            expert_id="correctness_business",
            name="Correctness",
            name_zh="正确性与业务专家",
            role="correctness",
            enabled=True,
            focus_areas=["业务正确性"],
            system_prompt="prompt",
        )
    ]
    captured: dict[str, object] = {}

    def fake_complete_text(**kwargs: object) -> LLMTextResult:
        captured["timeout_seconds"] = kwargs.get("timeout_seconds")
        captured["max_attempts"] = kwargs.get("max_attempts")
        return LLMTextResult(
            text='{"expert_routes":[{"expert_id":"correctness_business","candidate_id":"apps/api/orders/order.service.ts:1:1","routeable":true,"reason":"ok","confidence":0.9}],"skipped_experts":[]}',
            mode="live",
            provider="test",
            model="test",
            base_url="http://llm.test",
            api_key_env="TEST_KEY",
        )

    monkeypatch.setattr(agent._llm, "complete_text", fake_complete_text)

    runtime = RuntimeSettings(
        allow_llm_fallback=True,
        standard_llm_timeout_seconds=140,
        standard_llm_retry_count=5,
        light_llm_timeout_seconds=210,
        light_llm_retry_count=2,
    )
    plan = agent.build_routing_plan(subject, experts, runtime)

    assert plan["correctness_business"]["routing_source"] == "llm"
    assert captured["timeout_seconds"] == 210.0
    assert captured["max_attempts"] == 5


def test_main_agent_light_mode_skips_llm_routing_plan(monkeypatch):
    agent = MainAgentService()
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/design",
        target_ref="main",
        changed_files=["apps/api/orders/order.service.ts"],
        unified_diff=(
            "diff --git a/apps/api/orders/order.service.ts b/apps/api/orders/order.service.ts\n"
            "--- a/apps/api/orders/order.service.ts\n"
            "+++ b/apps/api/orders/order.service.ts\n"
            "@@ -1,2 +1,2 @@\n"
            "+ return payload\n"
        ),
    )
    experts = [
        ExpertProfile(
            expert_id="correctness_business",
            name="Correctness",
            name_zh="正确性与业务专家",
            role="correctness",
            enabled=True,
            focus_areas=["业务正确性"],
            system_prompt="prompt",
        )
    ]

    def fail_if_called(**_: object) -> LLMTextResult:
        raise AssertionError("轻量模式下不应再调用 routing_plan LLM")

    monkeypatch.setattr(agent._llm, "complete_text", fail_if_called)

    plan = agent.build_routing_plan(
        subject,
        experts,
        RuntimeSettings(allow_llm_fallback=True),
        analysis_mode="light",
    )

    assert plan["correctness_business"]["routing_source"] == "rule"
    assert plan["correctness_business"]["routing_llm"]["mode"] == "rule_only_light"


def test_main_agent_command_accepts_route_hint_with_full_business_files():
    agent = MainAgentService()
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/design",
        target_ref="main",
        changed_files=[
            "apps/api/orders/order.service.ts",
            "apps/api/orders/__tests__/order.service.test.ts",
            "packages/platform/types/order.output.ts",
        ],
        unified_diff="",
    )
    expert = ExpertProfile(
        expert_id="correctness_business",
        name="Correctness",
        name_zh="正确性与业务专家",
        role="correctness",
        enabled=True,
        focus_areas=["业务正确性"],
        system_prompt="prompt",
    )

    command = agent.build_command(
        subject,
        expert,
        RuntimeSettings(allow_llm_fallback=True),
        route_hint={
            "file_path": "packages/platform/types/order.output.ts",
            "line_start": 1,
            "target_hunk": {
                "file_path": "packages/platform/types/order.output.ts",
                "hunk_header": "@@ -1,2 +1,4 @@",
                "excerpt": "+ createdAt?: string",
            },
            "repo_hits": {},
            "routeable": True,
            "routing_reason": "字段契约变更",
            "confidence": 0.88,
        },
    )

    assert command["file_path"] == "packages/platform/types/order.output.ts"
    assert command["related_files"] == [
        "packages/platform/types/order.output.ts",
        "apps/api/orders/order.service.ts",
    ]
    assert command["routing_reason"] == "字段契约变更"


def test_main_agent_routing_prompt_includes_target_file_full_diff_and_related_summary():
    agent = MainAgentService()
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/full-diff",
        target_ref="main",
        changed_files=[
            "apps/api/orders/order.service.ts",
            "apps/api/orders/order.controller.ts",
        ],
        unified_diff=(
            "diff --git a/apps/api/orders/order.service.ts b/apps/api/orders/order.service.ts\n"
            "--- a/apps/api/orders/order.service.ts\n"
            "+++ b/apps/api/orders/order.service.ts\n"
            "@@ -1,3 +1,5 @@\n"
            " export async function createOrder(payload) {\n"
            "+  validateOrder(payload);\n"
            "+  auditCreate(payload.id);\n"
            "   return client.post('/orders', payload);\n"
            " }\n"
            "diff --git a/apps/api/orders/order.controller.ts b/apps/api/orders/order.controller.ts\n"
            "--- a/apps/api/orders/order.controller.ts\n"
            "+++ b/apps/api/orders/order.controller.ts\n"
            "@@ -3,1 +3,2 @@\n"
            "-router.post('/orders', createOrderHandler);\n"
            "+router.post('/orders', authGuard, createOrderHandler);\n"
        ),
    )
    experts = [
        ExpertProfile(
            expert_id="correctness_business",
            name="Correctness",
            name_zh="正确性与业务专家",
            role="correctness",
            enabled=True,
            focus_areas=["业务正确性"],
            system_prompt="prompt",
        )
    ]
    candidate_hunks = [
        {
            "candidate_id": "apps/api/orders/order.service.ts:2:1",
            "file_path": "apps/api/orders/order.service.ts",
            "line_start": 2,
            "hunk_header": "@@ -1,3 +1,5 @@",
            "excerpt": "+  validateOrder(payload);",
            "repo_hits": {},
        }
    ]

    prompt = agent._build_routing_user_prompt(
        subject=subject,
        experts=experts,
        candidate_hunks=candidate_hunks,
        runtime_settings=RuntimeSettings(),
    )

    assert "目标文件完整 diff" in prompt
    assert "validateOrder(payload);" in prompt
    assert "其他变更文件摘要" in prompt
    assert "authGuard" in prompt
    assert "语言通用规范提示" in prompt
    assert "JavaScript / TypeScript 通用代码规范" in prompt


def test_main_agent_expert_selection_prompt_uses_structured_diff_context():
    agent = MainAgentService()
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/full-diff",
        target_ref="main",
        changed_files=[
            "apps/api/orders/order.service.ts",
            "apps/api/orders/order.controller.ts",
        ],
        unified_diff=(
            "diff --git a/apps/api/orders/order.service.ts b/apps/api/orders/order.service.ts\n"
            "--- a/apps/api/orders/order.service.ts\n"
            "+++ b/apps/api/orders/order.service.ts\n"
            "@@ -1,3 +1,5 @@\n"
            " export async function createOrder(payload) {\n"
            "+  validateOrder(payload);\n"
            "+  auditCreate(payload.id);\n"
            "   return client.post('/orders', payload);\n"
            " }\n"
            "diff --git a/apps/api/orders/order.controller.ts b/apps/api/orders/order.controller.ts\n"
            "--- a/apps/api/orders/order.controller.ts\n"
            "+++ b/apps/api/orders/order.controller.ts\n"
            "@@ -3,1 +3,2 @@\n"
            "-router.post('/orders', createOrderHandler);\n"
            "+router.post('/orders', authGuard, createOrderHandler);\n"
        ),
    )
    experts = [
        ExpertProfile(
            expert_id="correctness_business",
            name="Correctness",
            name_zh="正确性与业务专家",
            role="correctness",
            enabled=True,
            focus_areas=["业务正确性"],
            system_prompt="prompt",
        )
    ]

    prompt = agent._build_expert_selection_user_prompt(
        subject=subject,
        experts=experts,
        requested_expert_ids=["correctness_business"],
        runtime_settings=RuntimeSettings(),
    )

    assert "业务变更文件完整 diff" in prompt
    assert "validateOrder(payload);" in prompt
    assert "authGuard" in prompt
    assert "Java 质量信号摘要" in prompt
    assert "变更源码与关联上下文" not in prompt
    assert "语言通用规范提示" in prompt
    assert "JavaScript / TypeScript 通用代码规范" in prompt


def test_main_agent_readds_security_expert_for_java_validation_signal():
    agent = MainAgentService()
    experts = [
        ExpertProfile(
            expert_id="security_compliance",
            name="Security",
            name_zh="安全与合规专家",
            role="security",
            enabled=True,
            focus_areas=["输入校验"],
            activation_hints=["auth", "security", "valid", "validation", "bindingresult"],
            required_checks=["输入校验是否被绕过"],
            system_prompt="prompt",
        ),
        ExpertProfile(
            expert_id="correctness_business",
            name="Correctness",
            name_zh="正确性与业务专家",
            role="correctness",
            enabled=True,
            focus_areas=["业务规则"],
            system_prompt="prompt",
        ),
    ]
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/validation",
        target_ref="main",
        changed_files=["src/main/java/com/example/UserController.java"],
        unified_diff=(
            "diff --git a/src/main/java/com/example/UserController.java b/src/main/java/com/example/UserController.java\n"
            "--- a/src/main/java/com/example/UserController.java\n"
            "+++ b/src/main/java/com/example/UserController.java\n"
            "@@ -18,1 +18,1 @@\n"
            "-public String create(@Valid UserRequest request, BindingResult result) {\n"
            "+public String create(UserRequest request, BindingResult result) {\n"
        ),
    )

    merged = agent._merge_expert_selection(
        subject=subject,
        experts=experts,
        requested_expert_ids=["security_compliance", "correctness_business"],
        llm_payload={
            "selected_experts": [
                {"expert_id": "correctness_business", "reason": "业务校验变化", "confidence": 0.9}
            ],
            "skipped_experts": [
                {"expert_id": "security_compliance", "reason": "LLM 认为不属于典型安全问题"}
            ],
        },
        fallback_ids=["correctness_business"],
    )

    assert "security_compliance" in merged["selected_expert_ids"]
    selected = {item["expert_id"]: item for item in merged["selected_experts"]}
    assert selected["security_compliance"]["source"] == "heuristic_selected"


def test_main_agent_readds_architecture_and_security_for_java_quality_signals():
    agent = MainAgentService()
    experts = [
        ExpertProfile(
            expert_id="correctness_business",
            name="Correctness",
            name_zh="正确性与业务专家",
            role="correctness",
            enabled=True,
            focus_areas=["业务规则"],
            system_prompt="prompt",
        ),
        ExpertProfile(
            expert_id="architecture_design",
            name="Architecture",
            name_zh="架构与设计专家",
            role="architecture",
            enabled=True,
            focus_areas=["分层架构"],
            system_prompt="prompt",
        ),
        ExpertProfile(
            expert_id="security_compliance",
            name="Security",
            name_zh="安全与合规专家",
            role="security",
            enabled=True,
            focus_areas=["安全边界"],
            system_prompt="prompt",
        ),
        ExpertProfile(
            expert_id="ddd_specification",
            name="DDD",
            name_zh="DDD规范专家",
            role="ddd",
            enabled=True,
            focus_areas=["聚合边界"],
            system_prompt="prompt",
        ),
    ]
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/composite",
        target_ref="main",
        changed_files=[
            "src/main/java/com/example/CourseCreator.java",
            "src/main/java/com/example/HibernateCriteriaConverter.java",
        ],
        unified_diff=(
            "diff --git a/src/main/java/com/example/CourseCreator.java b/src/main/java/com/example/CourseCreator.java\n"
            "--- a/src/main/java/com/example/CourseCreator.java\n"
            "+++ b/src/main/java/com/example/CourseCreator.java\n"
            "@@ -18,2 +18,3 @@\n"
            "-        Course course = Course.create(id, name, duration);\n"
            "+        Course course = new Course(id, name, duration);\n"
            "-        repository.save(course);\n"
            "+        eventBus.publish(course.pullDomainEvents());\n"
            "+        repository.save(course);\n"
            "diff --git a/src/main/java/com/example/HibernateCriteriaConverter.java b/src/main/java/com/example/HibernateCriteriaConverter.java\n"
            "--- a/src/main/java/com/example/HibernateCriteriaConverter.java\n"
            "+++ b/src/main/java/com/example/HibernateCriteriaConverter.java\n"
            "@@ -60,1 +60,1 @@\n"
            "-        return builder.equal(root.get(filter.field().value()), filter.value().value());\n"
            "+        return builder.like(root.get(filter.field().value()), String.format(\"%%%s%%\", filter.value().value()));\n"
        ),
    )

    merged = agent._merge_expert_selection(
        subject=subject,
        experts=experts,
        requested_expert_ids=["correctness_business", "architecture_design", "security_compliance", "ddd_specification"],
        llm_payload={
            "selected_experts": [
                {"expert_id": "correctness_business", "reason": "业务正确性变化明显", "confidence": 0.9},
                {"expert_id": "ddd_specification", "reason": "涉及聚合创建与事件顺序", "confidence": 0.88},
            ],
            "skipped_experts": [
                {"expert_id": "architecture_design", "reason": "LLM 认为只是实现细节"},
                {"expert_id": "security_compliance", "reason": "LLM 认为未命中安全关键词"},
            ],
        },
        fallback_ids=["correctness_business"],
    )

    assert "architecture_design" in merged["selected_expert_ids"]
    assert "security_compliance" in merged["selected_expert_ids"]
    selected = {item["expert_id"]: item for item in merged["selected_experts"]}
    assert selected["architecture_design"]["source"] == "heuristic_selected"
    assert selected["security_compliance"]["source"] == "heuristic_selected"


def test_main_agent_readds_maintainability_for_magic_value_and_naming_signals():
    agent = MainAgentService()
    experts = [
        ExpertProfile(
            expert_id="correctness_business",
            name="Correctness",
            name_zh="正确性与业务专家",
            role="correctness",
            enabled=True,
            focus_areas=["业务规则"],
            system_prompt="prompt",
        ),
        ExpertProfile(
            expert_id="maintainability_code_health",
            name="Maintainability",
            name_zh="可维护性与代码健康专家",
            role="maintainability",
            enabled=True,
            focus_areas=["复杂度", "命名"],
            system_prompt="prompt",
        ),
    ]
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/quality",
        target_ref="main",
        changed_files=["src/main/java/com/example/OrderService.java"],
        unified_diff=(
            "diff --git a/src/main/java/com/example/OrderService.java b/src/main/java/com/example/OrderService.java\n"
            "--- a/src/main/java/com/example/OrderService.java\n"
            "+++ b/src/main/java/com/example/OrderService.java\n"
            "@@ -40,3 +40,7 @@\n"
            '+        String orderStatusTmp = "MANUAL_RETRY";\n'
            "+        if (retryCount > 37) {\n"
            "+            return processWithPriority(orderStatusTmp, 86400);\n"
            "+        }\n"
        ),
    )

    merged = agent._merge_expert_selection(
        subject=subject,
        experts=experts,
        requested_expert_ids=["correctness_business", "maintainability_code_health"],
        llm_payload={
            "selected_experts": [
                {"expert_id": "correctness_business", "reason": "业务流程变化", "confidence": 0.88},
            ],
            "skipped_experts": [
                {"expert_id": "maintainability_code_health", "reason": "LLM 未关注语言层质量问题"},
            ],
        },
        fallback_ids=["correctness_business"],
    )

    assert "maintainability_code_health" in merged["selected_expert_ids"]
    selected = {item["expert_id"]: item for item in merged["selected_experts"]}
    assert selected["maintainability_code_health"]["source"] == "heuristic_selected"
