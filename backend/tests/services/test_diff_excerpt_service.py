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
