from app.services.diff_excerpt_service import DiffExcerptService


def test_extract_excerpt_ignores_patch_headers_and_returns_hunk_code():
    service = DiffExcerptService()
    diff = (
        "diff --git a/backend/app/api/debates.py b/backend/app/api/debates.py\n"
        "index 940ab63..b33ff0d 100644\n"
        "--- a/backend/app/api/debates.py\n"
        "+++ b/backend/app/api/debates.py\n"
        "@@ -468,7 +468,12 @@ async def execute_debate_background(\n"
        "         )\n"
        " \n"
        "     context = dict(session.context or {})\n"
        "-    context[\"execution_mode\"] = normalize_execution_mode(\"background\").value\n"
        "+    # 中文注释：后台执行只改变“投递方式”，不应覆盖用户原始选择的分析模式。\n"
        "+    context.setdefault(\n"
        "+        \"requested_execution_mode\",\n"
        "+        str(context.get(\"execution_mode\") or normalize_execution_mode(\"standard\").value),\n"
        "+    )\n"
        "+    context[\"execution_delivery_mode\"] = normalize_execution_mode(\"background\").value\n"
        "     session.context = context\n"
    )

    excerpt = service.extract_excerpt(diff, "backend/app/api/debates.py", 42)

    assert "iindex" not in excerpt
    assert "index 940ab63..b33ff0d 100644" not in excerpt
    assert "468 |" in excerpt
    assert "context = dict(session.context or {})" in excerpt


def test_find_nearest_line_returns_real_diff_line():
    service = DiffExcerptService()
    diff = (
        "diff --git a/backend/app/api/debates.py b/backend/app/api/debates.py\n"
        "index 940ab63..b33ff0d 100644\n"
        "--- a/backend/app/api/debates.py\n"
        "+++ b/backend/app/api/debates.py\n"
        "@@ -468,7 +468,12 @@ async def execute_debate_background(\n"
        "         )\n"
        " \n"
        "     context = dict(session.context or {})\n"
        "-    context[\"execution_mode\"] = normalize_execution_mode(\"background\").value\n"
        "+    context.setdefault(\n"
        "+        \"requested_execution_mode\",\n"
        "+        str(context.get(\"execution_mode\") or normalize_execution_mode(\"standard\").value),\n"
        "+    )\n"
        "+    context[\"execution_delivery_mode\"] = normalize_execution_mode(\"background\").value\n"
        "     session.context = context\n"
    )

    assert service.find_nearest_line(diff, "backend/app/api/debates.py", 42) == 471


def test_list_hunks_returns_hunk_header_and_changed_lines():
    service = DiffExcerptService()
    diff = (
        "diff --git a/apps/api/schedules/output.service.ts b/apps/api/schedules/output.service.ts\n"
        "--- a/apps/api/schedules/output.service.ts\n"
        "+++ b/apps/api/schedules/output.service.ts\n"
        "@@ -10,3 +10,5 @@ export const outputSchedule = () => {\n"
        "   const status = 'ok'\n"
        "+  const updatedAt = record.updatedAt\n"
        "+  return { status, updatedAt }\n"
        " }\n"
    )

    hunks = service.list_hunks(diff, "apps/api/schedules/output.service.ts")

    assert len(hunks) == 1
    assert hunks[0]["hunk_header"] == "@@ -10,3 +10,5 @@ export const outputSchedule = () => {"
    assert hunks[0]["changed_lines"] == [11, 12]


def test_find_best_hunk_prefers_closest_changed_lines():
    service = DiffExcerptService()
    diff = (
        "diff --git a/src/service.ts b/src/service.ts\n"
        "--- a/src/service.ts\n"
        "+++ b/src/service.ts\n"
        "@@ -10,2 +10,3 @@ export const one = () => {\n"
        "   return 'a'\n"
        "+  console.log('a')\n"
        " }\n"
        "@@ -30,2 +31,3 @@ export const two = () => {\n"
        "   return 'b'\n"
        "+  console.log('b')\n"
        " }\n"
    )

    hunk = service.find_best_hunk(diff, "src/service.ts", 31)

    assert hunk is not None
    assert hunk["start_line"] == 31


def test_list_hunks_ignores_patch_mail_headers_between_commits():
    service = DiffExcerptService()
    diff = (
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
        "From: Devin AI <158243242+devin-ai-integration[bot]@users.noreply.github.com>\n"
        "Date: Wed, 11 Mar 2026 20:09:08 +0000\n"
        "Subject: [PATCH 2/2] fix: add createdAt/updatedAt to Schedule type in\n"
        " getScheduleListItemData\n"
        "Co-Authored-By: joe@cal.com <j.auyeung419@gmail.com>\n"
    )

    hunks = service.list_hunks(diff, "packages/prisma/schema.prisma")

    assert len(hunks) == 1
    excerpt = str(hunks[0]["excerpt"])
    assert "Co-Authored-By" not in excerpt
    assert "Subject:" not in excerpt
    assert "@updatedAt" in excerpt
