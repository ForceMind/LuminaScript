"""Pure setup contracts shared by user input, AI drafts and candidate repairs."""
from decimal import Decimal, InvalidOperation, localcontext
from fractions import Fraction
import re
from typing import Any

MAX_FIELD_LENGTH = 20000
MAX_TOTAL_LENGTH = 60000
MAX_TITLE_LENGTH = 60
MIN_FIELD_LENGTHS = {"story_expansion": 24, "character_details": 12, "plot_details": 12}
PROJECT_TYPES = {"movie", "tv", "short", "short_video"}
COMMON_FIELDS = ("tone", "time_period", "story_expansion", "character_details", "plot_details", "title", "theme", "visual_style", "user_notes")
TYPE_FIELDS = {
    "movie": ("movie_duration", "scene_count_target"),
    "tv": ("episode_count", "episode_duration"),
    "short": ("episode_count", "episode_duration"),
    "short_video": ("video_duration_seconds",),
}
NUMERIC_LIMITS = {"movie_duration": (30, 300), "scene_count_target": (1, 200), "episode_count": (1, 100), "episode_duration": (1, 180), "video_duration_seconds": (15, 600)}
SETUP_FIELDS = set(COMMON_FIELDS) | set(NUMERIC_LIMITS) | {"project_type"}

# A conservative boilerplate detector, not a semantic classifier. Real stories
# need not mention a predefined verb, role, setting, or three-act structure.
GENERIC_RICH_PHRASES = (
    "情节丰富", "充满悬念", "引人入胜", "令人期待", "跌宕起伏", "扣人心弦",
    "戏剧张力", "精彩纷呈", "个性鲜明", "性格鲜明", "丰满立体", "关系复杂", "层次丰富",
    "令人印象深刻", "人物饱满", "发展方向", "精彩剧情", "具体内容待补充",
    "一系列波折", "一系列冒险", "人物成长", "推动情节发展", "意料之外的反转", "冲突激烈",
)
GENERIC_SCAFFOLD = re.compile(
    r"人物设定|关键情节|这将是|这是|这个|这组|一个|一段|一组|整体|主角|人物|故事|情节|高潮|"
    r"将保持|将经历|将设置|保持|经历|设置|通过|带来|拥有|具有|有着|推进|充满|富有|"
    r"而且|并且|以及|的|和|且|与|并"
)


def validate_rich_content(key: str, text: str) -> None:
    if re.fullmatch(r"\s*(?:TODO|TBD|待补充|待完善|暂无(?:内容)?|占位(?:内容|文本)?)[。.!！\s]*", text, flags=re.IGNORECASE):
        raise ValueError(f"字段 {key} 需要具体内容，不能使用占位说明")
    substantive = text
    for phrase in GENERIC_RICH_PHRASES:
        substantive = substantive.replace(phrase, "")
    if substantive == text:
        return  # No generic template evidence: never reject for vocabulary alone.
    substantive = GENERIC_SCAFFOLD.sub("", substantive)
    useful_length = len(re.sub(r"[\W_]+", "", substantive))
    if useful_length < (16 if key == "story_expansion" else 8):
        raise ValueError(f"字段 {key} 需要具体内容，不能只给出泛化评价或占位说明")


def field_contract(key: str) -> dict[str, Any]:
    """The same limits used by validation, included in every model field task."""
    if key not in SETUP_FIELDS:
        raise ValueError(f"不支持的设定字段: {key}")
    contract: dict[str, Any] = {
        "field": key, "value_type": "string",
        "minimum_characters": MIN_FIELD_LENGTHS.get(key, 0 if key == "user_notes" else 1),
        "maximum_characters": MAX_FIELD_LENGTH,
        "maximum_draft_characters": MAX_TOTAL_LENGTH,
    }
    if key in NUMERIC_LIMITS:
        minimum, maximum = NUMERIC_LIMITS[key]
        counts = key in {"scene_count_target", "episode_count"}
        seconds = key == "video_duration_seconds"
        contract.update(
            minimum=minimum, maximum=maximum,
            default_unit="count" if counts else "seconds" if seconds else "minutes",
            allowed_units=["正整数", "场" if key == "scene_count_target" else "集"] if counts else ["小时/hour/h", "分钟/minute/mins", "秒/second/s"],
            input_allows_decimal=not counts,
            canonical_allows_decimal=not counts and not seconds,
            canonical_format="正整数字符串" if counts else "整数秒字符串" if seconds else "精确分钟数字字符串+mins" if key == "episode_duration" else "精确分钟数字字符串",
            rules="仅单个数值；不接受负数、范围、多组数字；转换必须精确，秒转分钟须有限小数，不得四舍五入。",
        )
    elif key == "project_type":
        contract["allowed_values"] = sorted(PROJECT_TYPES)
    elif key == "title":
        contract.update(maximum_characters=MAX_TITLE_LENGTH, rules="仅题目本身，单行；去明确标题前缀和外层引号，保留标题内部冒号、破折号。")
    elif key in MIN_FIELD_LENGTHS:
        contract["rules"] = "value须给出具体叙事、场景事实、人物形象/关系或情节内容，不能只给泛化评价、占位或待补充说明；不强制三幕、动作词或职业词，镜头语言可以属于合法内容。"
    elif key == "user_notes":
        contract.update(empty_canonical_value="无", rules="无或空补充说明合法；空值规范为无。")
    return contract


def validate_safety(values: dict[str, Any], *, allowed: set[str] = SETUP_FIELDS) -> dict[str, str]:
    if not isinstance(values, dict):
        raise ValueError("设定字段必须为对象")
    for key, value in values.items():
        if key not in allowed:
            raise ValueError(f"不支持的设定字段: {key}")
        if not isinstance(value, str):
            raise ValueError(f"字段 {key} 必须为文本")
        if len(value) > MAX_FIELD_LENGTH:
            raise ValueError(f"字段 {key} 不能超过 {MAX_FIELD_LENGTH} 个字符")
    if sum(len(value) for value in values.values()) > MAX_TOTAL_LENGTH:
        raise ValueError(f"设定总内容不能超过 {MAX_TOTAL_LENGTH} 个字符")
    return dict(values)  # Safety checks never normalize locked user text.


def decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def normalize_number(key: str, raw: str) -> str:
    text = raw.strip().lower()
    if key in {"episode_count", "scene_count_target"}:
        unit = "集" if key == "episode_count" else "场"
        match = re.fullmatch(r"([0-9]+)\s*(?:" + unit + r")?", text)
        if not match:
            raise ValueError("数量必须是单个正整数，不能使用负数、小数或范围")
        value = Decimal(match[1])
    else:
        match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*(小时|时|hours?|hrs?|h|分钟|分|minutes?|mins?|min|m|秒钟|秒|seconds?|secs?|sec|s)?", text)
        if not match:
            raise ValueError("时长必须是单个非负数字及明确单位，不能使用范围或多组数字")
        unit = match[2] or ("秒" if key == "video_duration_seconds" else "分钟")
        factor = Decimal(3600 if unit in {"小时", "时", "hour", "hours", "hr", "hrs", "h"} else 1 if unit in {"秒钟", "秒", "second", "seconds", "sec", "secs", "s"} else 60)
        # Exact decimal arithmetic; reject non-terminating minute representations.
        try:
            with localcontext() as ctx:
                ctx.prec = max(32, len(match[1]) + 10)
                seconds = Decimal(match[1]) * factor
                denominator = (Fraction(seconds) / 60).denominator
                if key != "video_duration_seconds":
                    for prime in (2, 5):
                        while denominator % prime == 0:
                            denominator //= prime
                    if denominator != 1:
                        raise ValueError("秒数不能精确表示为有限小数分钟，请使用可精确转换的时长")
                value = seconds if key == "video_duration_seconds" else seconds / Decimal(60)
        except InvalidOperation as exc:
            raise ValueError("时长不是有效数字") from exc
        if key == "video_duration_seconds" and value != value.to_integral_value():
            raise ValueError("短视频时长必须精确到整数秒，不能自动取整")
    minimum, maximum = NUMERIC_LIMITS[key]
    if not minimum <= value <= maximum:
        raise ValueError(f"数值必须在 {minimum} 到 {maximum} 之间")
    return decimal_text(value) + ("mins" if key == "episode_duration" else "")


def normalize_title(raw: str) -> str:
    text = raw.strip()
    pairs = (("《", "》"), ("〈", "〉"), ("「", "」"), ("『", "』"), ("“", "”"), ("‘", "’"), ('"', '"'), ("'", "'"))
    while True:
        previous = text
        text = re.sub(r"^(?:(?:故事|剧本|作品|电影)?(?:标题|题目|片名|名称)|title)\s*(?:[:：]|为|是)\s*", "", text, flags=re.IGNORECASE)
        if len(text) >= 2 and any(text.startswith(left) and text.endswith(right) for left, right in pairs):
            text = text[1:-1].strip()
        if text == previous:
            break
    if not text or len(text) > MAX_TITLE_LENGTH or "\n" in text or "\r" in text:
        raise ValueError(f"故事题目须为 1–{MAX_TITLE_LENGTH} 个字符的单行标题")
    return text


def normalize_field(key: str, raw: Any) -> str:
    if raw is None:
        raw = ""
    if not isinstance(raw, str) or len(raw) > MAX_FIELD_LENGTH:
        raise ValueError(f"字段 {key} 必须为不超过 {MAX_FIELD_LENGTH} 字的文本")
    text = raw.strip()
    if key == "user_notes":
        return text or "无"
    if not text:
        raise ValueError(f"字段 {key} 不能为空")
    if key == "project_type":
        if text.lower() not in PROJECT_TYPES:
            raise ValueError("不支持的项目类型")
        return text.lower()
    if key in NUMERIC_LIMITS:
        normalized = normalize_number(key, text)
        if len(normalized) > MAX_FIELD_LENGTH:
            raise ValueError(f"字段 {key} 规范值过长")
        return normalized
    if key == "title":
        return normalize_title(text)
    if key not in SETUP_FIELDS:
        raise ValueError(f"不支持的设定字段: {key}")
    minimum = MIN_FIELD_LENGTHS.get(key, 1)
    if len(text) < minimum:
        raise ValueError(f"字段 {key} 至少需要 {minimum} 个字符")
    if key in {"character_details", "story_expansion", "plot_details"} and text in {"经典叙事风格", "带有反转的剧情", "大胆的实验性风格"}:
        raise ValueError(f"字段 {key} 需要具体故事内容")
    if key in {"character_details", "story_expansion", "plot_details"}:
        validate_rich_content(key, text)
    return text


def relevant_fields(project_type: str) -> tuple[str, ...]:
    return ("project_type", *TYPE_FIELDS[normalize_field("project_type", project_type)], *COMMON_FIELDS)


def normalize_complete(values: dict[str, Any]) -> dict[str, str]:
    validate_safety(values)
    fields = relevant_fields(values.get("project_type", ""))
    normalized = {key: normalize_field(key, values.get(key, "")) for key in fields}
    validate_safety(normalized)
    return normalized
