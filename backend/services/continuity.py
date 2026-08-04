from __future__ import annotations

import json
import re
from typing import Any, Iterable


INTERNAL_CONTEXT_KEYS = {
    "_scene_ai_prompts",
    "scene_prompt_cache",
    "next_step_cache",
    "continuity_state",
}


def compact_text(value: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def story_phase(scene_index: int, total_scenes: int) -> str:
    total = max(1, int(total_scenes or 1))
    progress = max(0.0, min(1.0, (int(scene_index or 1) - 1) / total))
    if progress < 0.12:
        return "开篇建立阶段"
    if progress < 0.28:
        return "冲突升级阶段"
    if progress < 0.52:
        return "中段推进阶段"
    if progress < 0.72:
        return "中后段反转阶段"
    if progress < 0.9:
        return "高潮逼近阶段"
    return "高潮与收束阶段"


def build_story_bible(
    *,
    logline: str,
    project_type: str,
    genre: str,
    global_context: dict[str, Any] | None,
) -> str:
    context = global_context or {}
    preferred_keys = (
        "synopsis_brief",
        "synopsis_detailed",
        "story_expansion",
        "character_details",
        "plot_details",
        "world_view",
        "tone",
        "time_period",
        "visual_style",
        "ending",
    )
    lines = [
        f"核心故事：{compact_text(logline, 1200)}",
        f"项目类型：{compact_text(project_type, 100)}",
        f"风格：{compact_text(genre, 600)}",
    ]
    seen = set()
    for key in preferred_keys:
        value = context.get(key)
        if value in (None, "", [], {}):
            continue
        seen.add(key)
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        lines.append(f"{key}：{compact_text(value, 1800)}")

    for key, value in context.items():
        if key in seen or key in INTERNAL_CONTEXT_KEYS:
            continue
        if value in (None, "", [], {}):
            continue
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        lines.append(f"{key}：{compact_text(value, 500)}")
        if sum(len(line) for line in lines) >= 9000:
            break
    return "\n".join(lines)[:10000]


def _scene_index(scene: Any) -> int:
    return int(getattr(scene, "scene_index", 0) or 0)


def _select_timeline_scenes(
    scenes: Iterable[Any],
    *,
    recent_count: int,
) -> list[Any]:
    ordered = sorted(scenes, key=_scene_index)
    if len(ordered) <= recent_count + 4:
        return ordered

    selected: dict[int, Any] = {}
    for scene in ordered[:3]:
        selected[_scene_index(scene)] = scene
    for scene in ordered[-recent_count:]:
        selected[_scene_index(scene)] = scene

    milestone_step = max(10, len(ordered) // 8)
    for scene in ordered:
        index = _scene_index(scene)
        if index % milestone_step == 0:
            selected[index] = scene
    return [selected[index] for index in sorted(selected)]


def build_outline_continuity_context(
    *,
    story_bible: str,
    prior_scenes: Iterable[Any],
    current_index: int,
    total_scenes: int,
    extra_warning: str = "",
) -> str:
    prior = list(prior_scenes)
    timeline = []
    for scene in _select_timeline_scenes(prior, recent_count=12):
        timeline.append(
            f"第{_scene_index(scene)}场："
            f"{compact_text(getattr(scene, 'outline', ''), 650)}"
        )
    previous = prior[-1] if prior else None
    previous_line = (
        f"第{_scene_index(previous)}场："
        f"{compact_text(getattr(previous, 'outline', ''), 1000)}"
        if previous
        else "无，这是第1场"
    )
    progress = round((max(1, current_index) - 1) / max(1, total_scenes) * 100)
    warning = compact_text(extra_warning, 800)
    return f"""【不可改变的故事圣经】
{story_bible}

【当前位置】
- 即将生成：第{current_index}场 / 共{total_scenes}场
- 故事进度：{progress}%
- 叙事阶段：{story_phase(current_index, total_scenes)}
- 紧接上一场：{previous_line}

【已发生的关键时间线】
{chr(10).join(timeline) if timeline else '尚无前序场次'}

【连续性硬约束】
- 这是同一个故事的第{current_index}场，不是新故事的第1场。
- 不得重新介绍已经登场的人物，不得重复开篇事件，不得让已完成的冲突无故重置。
- 必须承接上一场结束时的人物位置、关系、知识、伤情、道具和未解决线索。
- 推进当前叙事阶段，并为后续场次保留空间；不要提前大结局。
{warning}
"""[:18000]


def build_content_continuity_context(
    *,
    story_bible: str,
    completed_scenes: Iterable[Any],
    current_index: int,
    total_scenes: int,
) -> str:
    completed = list(completed_scenes)
    timeline = []
    for scene in _select_timeline_scenes(completed, recent_count=8):
        summary = getattr(scene, "summary", None) or getattr(scene, "outline", "")
        timeline.append(
            f"第{_scene_index(scene)}场：{compact_text(summary, 850)}"
        )

    previous = completed[-1] if completed else None
    previous_ending = "无，这是第1场"
    if previous:
        content = str(getattr(previous, "content", "") or "")
        previous_ending = (
            f"第{_scene_index(previous)}场摘要："
            f"{compact_text(getattr(previous, 'summary', '') or getattr(previous, 'outline', ''), 1000)}\n"
            f"上一场真实结尾：{compact_text(content[-1400:], 1400)}"
        )

    progress = round((max(1, current_index) - 1) / max(1, total_scenes) * 100)
    return f"""【不可改变的故事圣经】
{story_bible}

【当前位置】
- 当前场次：第{current_index}场 / 共{total_scenes}场
- 故事进度：{progress}%
- 叙事阶段：{story_phase(current_index, total_scenes)}

【前序关键时间线】
{chr(10).join(timeline) if timeline else '尚无前序场次'}

【必须直接承接的上一场】
{previous_ending}

【连续性硬约束】
- 从上一场最后动作或情绪结果继续，不要重新开篇或重新介绍人物。
- 人物只能知道此前已经获知的信息；伤情、关系、地点、道具与伏笔必须连续。
- 本场必须产生新变化，不得复述前文；结尾要留下能被下一场承接的明确状态。
"""[:18000]


def summarize_scene_for_continuity(outline: str, content: str) -> str:
    raw_content = str(content or "").strip()
    ending = compact_text(raw_content[-1200:], 1200)
    return compact_text(
        f"本场目标：{outline}；本场结束状态：{ending}",
        1600,
    )


RESTART_MARKERS = (
    "故事开始",
    "故事由此开始",
    "一切从这里开始",
    "序幕",
    "初次相遇",
    "第一次见面",
    "回到故事的起点",
)


def looks_like_story_restart(text: str, scene_index: int) -> bool:
    if int(scene_index or 0) < 8:
        return False
    # A genuine restart normally announces itself near the beginning. Limiting
    # the scan avoids rejecting a valid later reference such as “回想起序幕”.
    normalized = compact_text(text, 1200)
    return any(marker in normalized for marker in RESTART_MARKERS)
