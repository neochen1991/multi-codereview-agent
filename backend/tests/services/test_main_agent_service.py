from app.domain.models.expert_profile import ExpertProfile
from app.domain.models.review import ReviewSubject
from app.domain.models.runtime_settings import RuntimeSettings
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
    related.write_text("const updatedAt = schedule.updatedAt\n", encoding="utf-8")

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
