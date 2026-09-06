"""Canonical fields, local repairs, bounded refill and lossless usage audit."""
import json

from fastapi import BackgroundTasks, HTTPException
import pytest

import database
import main
import models
from services import llm, setup_fields as fields
from test_setup_modes import complete_movie_draft, seed_project


@pytest.fixture(autouse=True)
def isolate_ai(monkeypatch):
    async def no_network(*args, **kwargs):
        raise AssertionError("Unexpected real AI call")

    async def no_log(*args, **kwargs):
        return None

    monkeypatch.setattr(llm, "raw_generation", no_network)
    monkeypatch.setattr(main, "log_ai_action", no_log)


def draft(project_type="movie"):
    values = complete_movie_draft()
    values.update(project_type=project_type, episode_count="12", episode_duration="45mins", video_duration_seconds="60")
    return {key: values[key] for key in fields.relevant_fields(project_type)}


@pytest.mark.parametrize("key,raw,canonical", [
    ("movie_duration", "1.5小时", "90"), ("movie_duration", "90.5分钟", "90.5"),
    ("movie_duration", "5430s", "90.5"), ("episode_duration", "90秒", "1.5mins"),
    ("episode_duration", "1.5 hours", "90mins"), ("episode_duration", "1.5", "1.5mins"),
    ("video_duration_seconds", "2分钟", "120"), ("video_duration_seconds", "1.5mins", "90"),
    ("scene_count_target", "40场", "40"), ("episode_count", "12集", "12"),
])
def test_decimal_units_are_exact(key, raw, canonical):
    assert fields.normalize_field(key, raw) == canonical
    assert main.validate_interaction_answer(key, raw) == canonical
    if key.endswith("duration"):
        assert main.format_summary_value(key, raw) == canonical.removesuffix("mins") + " 分钟"


@pytest.mark.parametrize("key,raw", [
    ("movie_duration", "-90"), ("movie_duration", "90-120"), ("movie_duration", "90至120分钟"),
    ("movie_duration", "90分钟30秒"), ("movie_duration", "+90"), ("movie_duration", "1e2"),
    ("movie_duration", "1801秒"), ("episode_duration", "91秒"), ("episode_duration", "100秒"),
    ("episode_duration", "0.5mins"), ("episode_duration", "181mins"),
    ("episode_count", "1.5"), ("episode_count", "12.0"), ("episode_count", "-12"),
    ("scene_count_target", "0"), ("scene_count_target", "40/60"),
    ("video_duration_seconds", "0.251分钟"), ("video_duration_seconds", "14秒"),
    ("video_duration_seconds", "601秒"), ("video_duration_seconds", "2 minutes 3 seconds"),
])
def test_invalid_or_inexact_units_never_extract_first_number(key, raw):
    with pytest.raises(ValueError):
        fields.normalize_field(key, raw)


@pytest.mark.parametrize("raw,wanted", [
    ("标题：A", "A"), ("片名：《归途：城市—往事》", "归途：城市—往事"),
    ('“标题：《A：B-C》”', "A：B-C"), ("《片名：《A》》", "A"),
    ("暮色: 第二章—归来", "暮色: 第二章—归来"), ("title: 'A-B:C'", "A-B:C"),
])
def test_only_explicit_title_wrappers_are_removed(raw, wanted):
    assert main.extract_story_title(raw) == wanted
    assert fields.normalize_field("title", raw) == wanted


def test_optional_notes_and_single_character_phrases_are_valid():
    for value in ("", "   ", "无"):
        assert fields.normalize_field("user_notes", value) == "无"
    assert fields.normalize_field("theme", "爱") == "爱"
    assert fields.normalize_field("tone", "暖") == "暖"
    assert fields.normalize_field("character_details", "主角是一名研究镜头语言的导演，与姐姐争夺家庭秘密。")
    with pytest.raises(ValueError):
        fields.normalize_field("title", "题" * 61)


TARGETS = [(project_type, key) for project_type in ("movie", "tv", "short", "short_video")
           for key in fields.relevant_fields(project_type) if key != "project_type"]
assert len(TARGETS) == 43


def option_values(key):
    return {
        "movie_duration": ["90.5分钟", "2.5小时", "5400秒"],
        "scene_count_target": ["41场", "42", "43"], "episode_count": ["8集", "10", "20"],
        "episode_duration": ["90秒", "1.5小时", "2.5mins"],
        "video_duration_seconds": ["2分钟", "45秒", "1.5分钟"],
        "title": ["标题：新城：归来—甲", "《新城：归来—乙》", "片名：新城：归来—丙"],
        "user_notes": ["无", "只允许一处旁白", "不得使用闪回"],
    }.get(key, [complete_movie_draft().get(key, "新内容") + suffix for suffix in ("方案甲", "方案乙", "方案丙")])


@pytest.mark.asyncio
@pytest.mark.parametrize("project_type,target", TARGETS)
async def test_all_43_local_repair_targets_accept_empty_and_lock_other_raw_fields(monkeypatch, project_type, target):
    values = draft(project_type)
    values[target] = ""
    locked_key = "visual_style" if target == "theme" else "theme"
    values[locked_key] = "  爱  "
    captured = []

    async def options(step_key, question, context, **kwargs):
        captured.append(json.loads(context))
        return {"question": question, "options": [
            {"label": f"选择{i}", "value": value} for i, value in enumerate(option_values(step_key))
        ]}, 9

    monkeypatch.setattr(llm, "generate_interaction_options", options)
    async with database.SessionLocal() as session:
        user, project = await seed_project(session, context={main.SETUP_MODE_KEY: "ai_fast"})
        result = await main.revise_quick_setup_with_ai(1, main.QuickSetupAIReviseRequest(
            operation="regenerate_field", target_field=target, values=values,
            context_revision=project.context_revision,
        ), db=session, current_user=user)
        assert len(result["options"]) == 3
        assert len({item["value"] for item in result["options"]}) == 3
        assert captured[0]["current_draft"] == values
        assert values[locked_key] == "  爱  "
        assert project.global_context == {main.SETUP_MODE_KEY: "ai_fast"}
        assert project.context_revision == "setup-v2:0:0"
        assert project.total_tokens == 9


@pytest.mark.asyncio
async def test_partial_refill_keeps_good_options_excludes_old_and_audits_original(monkeypatch):
    calls, audits = [], []
    raw_first = '```json\n' + json.dumps({"question": "新题目", "options": [
        {"label": "旧", "value": "标题：A"}, {"label": "非法", "value": {"nested": "bad"}},
        {"label": "新一", "value": "《B：城市—往事》"},
        {"label": "重复", "value": "标题：B：城市—往事"},
        {"label": "非法列表", "value": ["bad"]}, {"label": "新二", "value": "片名：C"},
    ]}, ensure_ascii=False) + '\n```'
    raw_second = '{"question":"新题目","options":[{"label":"新三","value":"D"}]}'

    async def raw(messages, **kwargs):
        calls.append(messages[1]["content"])
        return (raw_first, 5) if len(calls) == 1 else (raw_second, 7)

    async def audit(**kwargs):
        audits.append(kwargs)

    monkeypatch.setattr(llm, "raw_generation", raw)
    monkeypatch.setattr(main, "log_ai_action", audit)
    async with database.SessionLocal() as session:
        user, project = await seed_project(session)
        values = draft()
        values["title"] = "A"
        result = await main.revise_quick_setup_with_ai(1, main.QuickSetupAIReviseRequest(
            operation="regenerate_field", target_field="title", values=values, context_revision=project.context_revision,
        ), db=session, current_user=user)
        assert [item["value"] for item in result["options"]] == ["B：城市—往事", "C", "D"]
        assert '"required_count": 1' in calls[1]
        assert 'B：城市—往事' in calls[1] and '"A"' in calls[1]
        assert result["tokens_used"] == project.total_tokens == 12
        assert [item["status"] for item in audits] == ["partial", "success"]
        assert [item["response"] for item in audits] == [raw_first, raw_second]


@pytest.mark.asyncio
@pytest.mark.parametrize("old_notes", [None, "无"])
async def test_guided_absent_notes_do_not_exclude_none_but_explicit_old_none_does(monkeypatch, old_notes):
    calls = []

    async def options(step_key, question, context, **kwargs):
        calls.append(json.loads(context))
        texts = ["无", "允许旁白", "不要闪回"] if len(calls) == 1 else ["不得增添人物"]
        return {"question": question, "options": [{"label": value, "value": value} for value in texts]}, 1

    monkeypatch.setattr(llm, "generate_interaction_options", options)
    async with database.SessionLocal() as session:
        user, project = await seed_project(session)
        values = draft()
        if old_notes is None:
            values.pop("user_notes")
        else:
            values["user_notes"] = old_notes
        result, usage = await main.generate_validated_setup_options(
            db=session, project=project, current_user=user, step_key="user_notes", question="补充？",
            values=values, context="全上下文", revision=project.context_revision, template_instructions="", action="analyze_step_user_notes",
        )
        assert ("无" in [item["value"] for item in result["options"]]) is (old_notes is None)
        assert len(calls) == (1 if old_notes is None else 2)


@pytest.mark.asyncio
async def test_review_repairs_invalid_edited_field_without_normalizing_locked_fields(monkeypatch):
    values = draft()
    values.update(theme="", character_details="短", tone="  暖  ", user_notes="")

    async def review(**kwargs):
        assert kwargs["values"] == values
        assert kwargs["allowed_fields"] == ["theme"]
        return {"theme": "爱"}, "修复主题", 4

    monkeypatch.setattr(llm, "revise_quick_setup_fields", review)
    async with database.SessionLocal() as session:
        user, project = await seed_project(session)
        result = await main.revise_quick_setup_with_ai(1, main.QuickSetupAIReviseRequest(
            operation="review_edits", values=values, edited_fields=["theme"], context_revision=project.context_revision,
            baseline_values={**values, "theme": "修改前的有效主题"},
        ), db=session, current_user=user)
        assert result["changes"] == [{"field": "theme", "before": "", "after": "爱"}]
        assert values["tone"] == "  暖  "
        values["theme"] = "爱"
        with pytest.raises(HTTPException) as error:
            await main.submit_quick_setup_review(1, main.QuickSetupReviewRequest(values=values, context_revision=project.context_revision), db=session, current_user=user)
        assert error.value.status_code == 422
        values["character_details"] = complete_movie_draft()["character_details"]
        await main.submit_quick_setup_review(1, main.QuickSetupReviewRequest(values=values, context_revision=project.context_revision), db=session, current_user=user)
        assert project.global_context["user_notes"] == "无"
        assert project.global_context["tone"] == "暖"


@pytest.mark.asyncio
async def test_json_failure_usage_and_raw_text_survive_retry(monkeypatch):
    calls, audits = [], []
    raw_bad = "provider original response: this is not JSON"
    good = {"question": "主题", "options": [{"label": value, "value": value} for value in ("爱", "勇气", "自由")]}

    async def raw(*args, **kwargs):
        calls.append(True)
        return (raw_bad, 7) if len(calls) == 1 else (json.dumps(good, ensure_ascii=False), 9)

    async def audit(**kwargs):
        audits.append(kwargs)

    monkeypatch.setattr(llm, "raw_generation", raw)
    monkeypatch.setattr(main, "log_ai_action", audit)
    async with database.SessionLocal() as session:
        user, project = await seed_project(session)
        result = await main.revise_quick_setup_with_ai(1, main.QuickSetupAIReviseRequest(
            operation="regenerate_field", target_field="theme", values=draft(), context_revision=project.context_revision,
        ), db=session, current_user=user)
        assert project.total_tokens == result["tokens_used"] == 16
        assert audits[0]["tokens"] == 7 and audits[0]["response"] == raw_bad
        assert audits[0]["error_type"] == "json_parse_failed"


@pytest.mark.asyncio
async def test_oversized_initial_drafts_fail_before_display_and_charge_each_attempt(monkeypatch):
    audits = []
    values = draft()
    values["story_expansion"] = "长" * 20001
    raw_text = json.dumps({"fields": values}, ensure_ascii=False)

    async def raw(*args, **kwargs):
        return raw_text, 5

    async def audit(**kwargs):
        audits.append(kwargs)

    monkeypatch.setattr(llm, "raw_generation", raw)
    monkeypatch.setattr(main, "log_ai_action", audit)
    async with database.SessionLocal() as session:
        user, project = await seed_project(session, context={main.SETUP_MODE_KEY: "ai_fast"})
        result = await main.analyze_logline(1, BackgroundTasks(), db=session, current_user=user)
        assert result["payload"]["field"] == "setup_mode"
        assert project.next_step_cache is None
        assert project.total_tokens == 10
        assert len(audits) == 2 and all(item["response"] == raw_text and item["tokens"] == 5 for item in audits)


def test_request_and_output_share_strict_length_and_type_safety():
    values = draft()
    values.update(story_expansion="长" * 20000, character_details="人" * 20000, plot_details="情" * 20000)
    for validate in (fields.normalize_complete, fields.validate_safety):
        with pytest.raises(ValueError):
            validate(values)
    with pytest.raises(ValueError):
        main.QuickSetupAIReviseRequest(operation="regenerate_field", values={"tone": "x" * 20001}, target_field="tone", context_revision="setup-v2:0:0")
    parsed = llm._normalize_interaction_payload("character_details", "人物？", {"options": [
        {"label": "主角的关系秘密身份目标都是完整描述", "value": "短"},
        {"label": "人物", "value": {"name": "人物"}},
        {"label": "人物", "value": "主角是一位擅长镜头语言的导演，与失散姐姐合作追查旧案。"},
    ]}, strict=True, allow_partial=True)
    assert len(parsed["options"]) == 1


@pytest.mark.asyncio
async def test_revision_error_carries_usage_and_unmodified_provider_text(monkeypatch):
    original = '```json\n{"fields":{"tone":"越界"},"summary":"原文"}\n```'

    async def raw(*args, **kwargs):
        return llm.AIText('{"fields":{"tone":"越界"},"summary":"原文"}', original), 13

    monkeypatch.setattr(llm, "raw_generation", raw)
    with pytest.raises(llm.InteractionGenerationError) as error:
        await llm.revise_quick_setup_fields(logline="故事", values=draft(), allowed_fields=["theme"])
    assert error.value.usage == 13
    assert error.value.raw_content == original


@pytest.mark.asyncio
async def test_prefill_parse_failure_keeps_raw_usage_and_safe_guided_fallback(monkeypatch):
    audits = []
    original = "prefill provider original non-JSON text"

    async def raw(*args, **kwargs):
        return original, 6

    async def audit(**kwargs):
        audits.append(kwargs)

    monkeypatch.setattr(llm, "raw_generation", raw)
    monkeypatch.setattr(main, "log_ai_action", audit)
    async with database.SessionLocal() as session:
        user, project = await seed_project(session, context={main.SETUP_MODE_KEY: "guided"})
        project.logline = "这是需要从长输入中整理设定的创意，并不是一个短标题。" * 8
        await session.commit()
        result = await main.analyze_logline(1, BackgroundTasks(), db=session, current_user=user)
        assert result["payload"]["field"] == "project_type"
        assert project.total_tokens == 6
        assert audits[0]["status"] == "failed"
        assert audits[0]["response"] == original
        assert audits[0]["tokens"] == 6


def test_prefill_merge_does_not_exceed_total_length_with_existing_fields():
    project = models.Project(title="", logline="", project_type="pending", global_context={
        "story_expansion": "情" * 20000, "character_details": "人" * 20000, "tone": "暖" * 19900,
    })
    filled, _ = main.apply_auto_prefill(project, {"plot_details": "新情节" * 300, "user_notes": ""})
    assert "plot_details" not in filled
    assert "plot_details" not in project.global_context
    fields.validate_safety({key: value for key, value in project.global_context.items() if key in fields.SETUP_FIELDS})


def test_parser_does_not_hide_overlong_raw_values_by_stripping_whitespace():
    data = llm._normalize_interaction_payload("tone", "基调", {"options": [
        {"label": "候选", "value": " " * 20000 + "暖"},
        {"label": "合法", "value": "暖"},
    ]}, strict=True, allow_partial=True)
    assert data["options"] == [{"label": "合法", "value": "暖"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("target,bad", [("movie_duration", "90-120分钟"), ("character_details", "短"), ("title", "题" * 61)])
async def test_nonempty_semantically_invalid_target_can_be_repaired(monkeypatch, target, bad):
    calls = []

    async def options(step_key, question, context, **kwargs):
        calls.append(json.loads(context))
        return {"question": question, "options": [{"label": str(index), "value": value} for index, value in enumerate(option_values(step_key))]}, 2

    monkeypatch.setattr(llm, "generate_interaction_options", options)
    async with database.SessionLocal() as session:
        user, project = await seed_project(session)
        values = draft()
        values[target] = bad
        result = await main.revise_quick_setup_with_ai(1, main.QuickSetupAIReviseRequest(
            operation="regenerate_field", target_field=target, values=values, context_revision=project.context_revision,
        ), db=session, current_user=user)
        assert len(result["options"]) == 3
        assert calls[0]["current_draft"][target] == bad
        assert project.global_context == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("project_type,duration_key,duration,count", [
    ("movie", "movie_duration", "91.5", 73),
    ("short_video", "video_duration_seconds", "2分钟", 8),
])
async def test_generation_queue_uses_precise_duration_semantics(project_type, duration_key, duration, count):
    async with database.SessionLocal() as session:
        context = draft(project_type)
        context.update({
            duration_key: duration,
            "final_confirm": "confirmed",
            "synopsis_brief": "已存在梗概",
            "synopsis_detailed": "已存在详细梗概",
        })
        if project_type == "movie":
            context["scene_count_target"] = str(count)
        user, project = await seed_project(
            session,
            project_type=project_type,
            context=context,
        )
        result = await main.generate_scenes(
            1,
            selected_option="auto",
            context_revision=project.context_revision,
            db=session,
            current_user=user,
        )
        job = await session.get(models.GenerationJob, result["job_id"])
        assert job.payload["target_count"] == count
        if project_type == "short_video":
            assert "总时长:120秒" in job.payload["style_context"]


GENERIC_RICH_OPTIONS = {
    "story_expansion": [
        "这是一个情节丰富、充满悬念、引人入胜且令人期待的发展方向。",
        "这将是一段跌宕起伏、扣人心弦且充满戏剧张力的精彩剧情。",
        "故事将经历一系列波折，带来层次丰富、精彩纷呈并令人期待的发展方向。",
    ],
    "character_details": [
        "这组人物个性鲜明、丰满立体、关系复杂而且令人印象深刻。",
        "主角性格鲜明、丰满立体，整体人物饱满，富有戏剧张力且令人期待。",
        "人物设定将保持层次丰富和关系复杂，整体个性鲜明并令人印象深刻。",
    ],
    "plot_details": [
        "关键情节将设置意料之外的反转，整体冲突激烈且高潮精彩纷呈。",
        "这个发展方向充满悬念，情节丰富，拥有令人期待和扣人心弦的高潮。",
        "情节将保持戏剧张力，通过层次丰富的推进带来精彩纷呈的发展方向。",
    ],
}


@pytest.mark.asyncio
@pytest.mark.parametrize("target", list(GENERIC_RICH_OPTIONS))
async def test_three_long_generic_rich_options_are_rejected_after_only_one_refill(monkeypatch, target):
    calls, audits = [], []
    raw_text = json.dumps({"question": "具体设定", "options": [
        {"label": f"具体主角秘密转折高潮{i}", "value": value}
        for i, value in enumerate(GENERIC_RICH_OPTIONS[target])
    ]}, ensure_ascii=False)

    async def raw(*args, **kwargs):
        calls.append(True)
        return raw_text, 5

    async def audit(**kwargs):
        audits.append(kwargs)

    monkeypatch.setattr(llm, "raw_generation", raw)
    monkeypatch.setattr(main, "log_ai_action", audit)
    async with database.SessionLocal() as session:
        user, project = await seed_project(session)
        with pytest.raises(HTTPException) as error:
            await main.revise_quick_setup_with_ai(1, main.QuickSetupAIReviseRequest(
                operation="regenerate_field", target_field=target, values=draft(), context_revision=project.context_revision,
            ), db=session, current_user=user)
        assert error.value.status_code == 503
        assert "0 个有效新选项" in error.value.detail
        assert len(calls) == 2
        assert project.total_tokens == 10
        assert project.global_context == {}
        assert project.next_step_cache is None
        assert len(audits) == 2
        assert all(item["response"] == raw_text and item["tokens"] == 5 for item in audits)


@pytest.mark.parametrize("key,text", [
    ("story_expansion", "小女孩把最后一颗糖放到老人的掌心，老人取出旧照片，两人同时认出了照片中的母亲。"),
    ("story_expansion", "镜头扫过破旧雨伞，外卖员把唯一的雨衣盖在流浪狗身上，自己淋雨骑车离开。"),
    ("story_expansion", "一只纸船顺着积水漂进地铁口，被老人拦住放进玻璃柜，镜头最后停在泛黄的车票上。"),
    ("character_details", "主角是一名研究镜头语言的导演，与姐姐争夺家庭秘密。"),
    ("character_details", "阿宁，三十五岁，寡言的修伞匠，攒钱替妹妹治病，遇事总先把责任揽在自己身上。"),
    ("plot_details", "在老站长销毁档案之前，记者必须把母亲留下的车票交给失踪的哥哥。"),
    ("plot_details", "一名富豪独居大豪宅，生活完全自动化，由机器人负责送饭和送物，资产及收益由AI管理。附近平民依靠捡拾其丢弃的物品和剩鲜饭菜生活。"),
    ("character_details", "阿岚，黑色短发，沉着勇敢，对妹妹百般照顾。"),
    ("story_expansion", "孩子们围着铜炉唱起古老歌谣，火光映红了墙上的壁画，山谷里传来祖辈熟悉的回声。"),
])
def test_concrete_short_form_narratives_and_portraits_need_no_three_act_template(key, text):
    assert fields.normalize_field(key, text) == text


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["regenerate", "partial_regenerate", "failed_parse", "review", "review_invalid", "quick_draft", "guided", "prefill"])
async def test_setup_audit_failure_is_503_keeps_usage_and_stops_model_calls(monkeypatch, operation):
    model_calls, audit_calls = [], []

    async def options(*args, **kwargs):
        model_calls.append(True)
        if operation == "failed_parse":
            raise llm.InteractionGenerationError("invalid provider JSON", raw_content="RAW failure", usage=7)
        choices = ["爱"] if operation == "partial_regenerate" else ["爱", "勇气", "自由"]
        return {"question": "新方向", "options": [{"label": value, "value": value} for value in choices]}, 7

    async def review(**kwargs):
        model_calls.append(True)
        return ({"theme": "爱", "title": "越界"} if operation == "review_invalid" else {"theme": "爱"}), "建议", 7

    async def quick_draft(**kwargs):
        model_calls.append(True)
        return draft(), 7

    async def prefill(*args, **kwargs):
        model_calls.append(True)
        return {"tone": "暖"}, 7

    async def failing_audit(**kwargs):
        audit_calls.append(kwargs)
        raise RuntimeError("synthetic audit storage unavailable")

    monkeypatch.setattr(llm, "generate_interaction_options", options)
    monkeypatch.setattr(llm, "revise_quick_setup_fields", review)
    monkeypatch.setattr(llm, "generate_quick_setup_draft", quick_draft)
    monkeypatch.setattr(llm, "extract_setup_from_long_input", prefill)
    monkeypatch.setattr(main, "log_ai_action", failing_audit)
    async with database.SessionLocal() as session:
        setup_mode = "guided" if operation in {"guided", "prefill"} else "ai_fast"
        context = {main.SETUP_MODE_KEY: setup_mode}
        if operation == "guided":
            context.update(movie_duration="90", scene_count_target="60")
        user, project = await seed_project(session, project_type="movie" if operation == "guided" else "pending", context=context)
        if operation == "prefill":
            project.logline = "这是一个需要预填设定的长故事输入，用来验证日志存储故障不会应用模型返回。" * 6
            await session.commit()
        original_context = dict(project.global_context)
        original_revision = project.context_revision
        with pytest.raises(HTTPException) as error:
            if operation in {"quick_draft", "guided", "prefill"}:
                await main.analyze_logline(1, BackgroundTasks(), db=session, current_user=user)
            else:
                await main.revise_quick_setup_with_ai(1, main.QuickSetupAIReviseRequest(
                    operation="review_edits" if operation.startswith("review") else "regenerate_field",
                    target_field=None if operation.startswith("review") else "theme",
                    edited_fields=["theme"] if operation.startswith("review") else [],
                    values=draft(), context_revision=original_revision,
                ), db=session, current_user=user)
        assert error.value.status_code == 503
        assert "审计写入失败" in error.value.detail
        assert "未能确认保存" in error.value.detail
        assert len(model_calls) == len(audit_calls) == 1
        await session.refresh(project)
        assert project.total_tokens == 7
        assert project.global_context == original_context
        assert project.context_revision == original_revision
        assert project.next_step_cache is None


@pytest.mark.asyncio
async def test_refill_prompt_includes_same_source_contract_and_bounded_parser_rejections(monkeypatch):
    contexts = []
    provider_calls = []
    actual_options = llm.generate_interaction_options

    async def inspect_options(step_key, question, context, **kwargs):
        contexts.append(json.loads(context))
        return await actual_options(step_key, question, context, **kwargs)

    async def raw(*args, **kwargs):
        provider_calls.append(True)
        values = ["90-120分钟", "-90", "9999分钟"] if len(provider_calls) == 1 else option_values("movie_duration")
        return json.dumps({"question": "电影时长", "options": [{"label": value, "value": value} for value in values]}, ensure_ascii=False), 3

    monkeypatch.setattr(llm, "generate_interaction_options", inspect_options)
    monkeypatch.setattr(llm, "raw_generation", raw)
    async with database.SessionLocal() as session:
        user, project = await seed_project(session)
        result = await main.revise_quick_setup_with_ai(1, main.QuickSetupAIReviseRequest(
            operation="regenerate_field", target_field="movie_duration", values=draft(), context_revision=project.context_revision,
        ), db=session, current_user=user)
        assert len(result["options"]) == 3
        assert len(provider_calls) == 2
        assert contexts[0]["field_contract"] == fields.field_contract("movie_duration")
        contract = contexts[1]["field_contract"]
        assert contract["minimum"] == fields.NUMERIC_LIMITS["movie_duration"][0]
        assert contract["maximum"] == fields.NUMERIC_LIMITS["movie_duration"][1]
        assert contract["default_unit"] == "minutes" and contract["canonical_allows_decimal"] is True
        assert contexts[0]["rejection_summary"] == []
        summary = contexts[1]["rejection_summary"]
        assert 1 <= len(summary) <= 8
        assert all(len(item["reason"]) <= 160 for item in summary)
        assert sum(item["count"] for item in summary) == 3
        assert any("数值必须在" in item["reason"] for item in summary)
        assert any("不能使用范围" in item["reason"] for item in summary)


@pytest.mark.asyncio
async def test_initial_draft_prompt_keeps_canonical_field_contracts(monkeypatch):
    specs = main.quick_setup_field_specs()
    assert all(item["contract"] == fields.field_contract(item["key"]) for item in specs)

    async def raw(messages, **kwargs):
        prompt = messages[1]["content"]
        for key in ("title", "episode_duration", "video_duration_seconds", "story_expansion"):
            assert json.dumps(fields.field_contract(key), ensure_ascii=False) in prompt
        return json.dumps({"fields": draft()}, ensure_ascii=False), 1

    monkeypatch.setattr(llm, "raw_generation", raw)
    await llm.generate_quick_setup_draft(logline="创意", current_context={}, field_specs=specs)
    assert fields.field_contract("title")["maximum_characters"] == fields.MAX_TITLE_LENGTH
    video = fields.field_contract("video_duration_seconds")
    assert video["input_allows_decimal"] is True
    assert video["canonical_allows_decimal"] is False
    assert fields.field_contract("story_expansion")["minimum_characters"] == fields.MIN_FIELD_LENGTHS["story_expansion"]
