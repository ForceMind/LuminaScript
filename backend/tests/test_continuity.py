from types import SimpleNamespace

from services.continuity import (
    build_content_continuity_context,
    build_outline_continuity_context,
    looks_like_story_restart,
    summarize_scene_for_continuity,
)


def make_scene(index: int, *, content: str = ""):
    return SimpleNamespace(
        scene_index=index,
        outline=f"主角推进第{index}步并保留线索{index}",
        summary=f"第{index}场结束时主角掌握线索{index}",
        content=content or f"第{index}场正文，结尾状态{index}",
    )


def test_long_outline_context_keeps_bible_milestones_and_recent_scenes():
    scenes = [make_scene(index) for index in range(1, 51)]
    context = build_outline_continuity_context(
        story_bible="主角必须找到失踪的妹妹，反派身份不能改变。",
        prior_scenes=scenes,
        current_index=51,
        total_scenes=100,
    )

    assert "第51场 / 共100场" in context
    assert "中段推进阶段" in context
    assert "主角必须找到失踪的妹妹" in context
    assert "第50场" in context
    assert "不是新故事的第1场" in context
    assert len(context) < 18000


def test_content_context_uses_previous_real_ending():
    scenes = [make_scene(index) for index in range(1, 50)]
    scenes[-1].content = "场景前部。门被锁死，林夏发现钥匙在反派手中。"
    context = build_content_continuity_context(
        story_bible="固定故事圣经",
        completed_scenes=scenes,
        current_index=50,
        total_scenes=80,
    )

    assert "第50场 / 共80场" in context
    assert "钥匙在反派手中" in context
    assert "不要重新开篇" in context
    assert len(context) < 18000


def test_scene_summary_preserves_actual_ending_and_restart_detection():
    summary = summarize_scene_for_continuity(
        "主角进入仓库",
        "前文。最终主角负伤，证据被同伴带走。",
    )

    assert "证据被同伴带走" in summary
    assert looks_like_story_restart("序幕：故事由此开始", 50) is True
    assert looks_like_story_restart("序幕：故事由此开始", 2) is False
