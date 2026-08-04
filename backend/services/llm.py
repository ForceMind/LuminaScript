from openai import AsyncOpenAI
import json
import logging
import re

import asyncio
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from services.llm_config import LLMRuntimeConfig, get_routed_llm_configs

# Configure Configuration
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_client_cache: dict[tuple[str, str, int], AsyncOpenAI] = {}
_semaphore_cache: dict[tuple[int, int], asyncio.Semaphore] = {}


def _get_client(config: LLMRuntimeConfig) -> AsyncOpenAI:
    if not config.api_key:
        raise RuntimeError("AI API Key 未配置，请管理员在后台的 AI 配置页面中设置。")
    cache_key = (config.api_key, config.base_url, config.timeout_seconds)
    runtime_client = _client_cache.get(cache_key)
    if runtime_client is None:
        runtime_client = AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=config.timeout_seconds,
            max_retries=0,
        )
        _client_cache[cache_key] = runtime_client
    return runtime_client


def _get_semaphore(max_concurrency: int) -> asyncio.Semaphore:
    loop_key = id(asyncio.get_running_loop())
    cache_key = (loop_key, max_concurrency)
    semaphore = _semaphore_cache.get(cache_key)
    if semaphore is None:
        semaphore = asyncio.Semaphore(max_concurrency)
        _semaphore_cache[cache_key] = semaphore
    return semaphore

_PORN_PATTERNS = [
    r'色情', r'裸聊', r'约炮', r'性奴', r'乱伦', r'援交',
    r'porn', r'nsfw', r'sex', r'nude', r'erotic'
]

_HARMFUL_VALUE_PATTERNS = [
    r'仇恨', r'种族清洗', r'纳粹', r'极端主义', r'恐怖袭击',
    r'歧视', r'虐杀', r'教唆犯罪', r'鼓吹暴力', r'辱女', r'辱童',
    r'hate speech', r'terror', r'extremist', r'racist'
]

def _contains_any_pattern(text: str, patterns: list[str]) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)

def _fallback_rewrite(text: str, categories: list[str]) -> str:
    rewritten = text
    if "色情信息" in categories:
        for p in _PORN_PATTERNS:
            rewritten = re.sub(p, "情感冲突", rewritten, flags=re.IGNORECASE)
    if "价值观风险" in categories:
        for p in _HARMFUL_VALUE_PATTERNS:
            rewritten = re.sub(p, "价值冲突", rewritten, flags=re.IGNORECASE)
    if rewritten.strip() == text.strip():
        rewritten = (
            "请将该创意调整为积极、健康、合规的剧情方向，"
            "保留核心冲突但避免色情和极端价值表达。"
        )
    return rewritten


def _build_interaction_fallback(step_key: str, base_question: str):
    if step_key == "character_details":
        return {
            "question": base_question,
            "options": [
                {
                    "label": "单主角对抗型",
                    "value": "- 主角：一个被异常事件卷入的普通人，外表克制，内心倔强，核心目标是查清真相并保护自己最在乎的人。\n- 对手：掌握规则和资源的强势人物，表面理性克制，实则把主角当成可替换的工具。\n- 关键配角：与主角关系最亲近的同伴，既提供帮助，也会在关键时刻因为恐惧或利益产生动摇。"
                },
                {
                    "label": "双主角互补型",
                    "value": "- 主角A：行动力强，敢于冒险，但容易冲动做决定。\n- 主角B：理性谨慎，擅长分析，却长期压抑真实情感。\n- 对手：熟悉系统规则的操盘者，最擅长利用两位主角之间的不信任。\n- 配角：负责提供线索与情感支点，推动两位主角从互相试探走向真正结盟。"
                },
                {
                    "label": "群像关系型",
                    "value": "- 核心人物：一个看似最普通的人，却意外成为所有冲突的交汇点。\n- 对立人物：代表既有秩序和现实压力的人物，始终试图让故事回到可控范围。\n- 关键配角1：拥有重要秘密，表面沉默，实际掌握破局线索。\n- 关键配角2：情感立场摇摆不定，既可能帮助主角，也可能成为压垮局面的导火索。"
                }
            ]
        }

    if step_key == "story_expansion":
        return {
            "question": base_question,
            "options": [
                {
                    "label": "经典三幕推进",
                    "value": "第一幕：主角在日常秩序中暴露核心困境，被迫卷入异常事件，并在第一次失败后意识到自己无法回头。\n第二幕：主角不断追查真相，结识盟友也树立敌人，表面上逐步接近答案，实际上被更大的局操控。\n第三幕：主角在失去关键依靠后完成反击，用新的认知直面终极冲突，并以带有代价的胜利完成人物弧光。"
                },
                {
                    "label": "悬念递进型",
                    "value": "第一幕：抛出一个强悬念，让主角因偶然发现进入危险局面。\n第二幕：每次推进都会揭开更大一层误导，真相与主角最初理解完全相反，人物关系也随之重组。\n第三幕：主角识破核心谎言，在极限处境中做出关键选择，既解决表层危机，也揭示故事真正命题。"
                },
                {
                    "label": "情感反噬型",
                    "value": "第一幕：主角出于个人情感或现实欲望踏出第一步，以为自己只是在解决一个具体问题。\n第二幕：事件不断升级，主角与亲密关系逐渐撕裂，最初的选择开始反噬自己。\n第三幕：主角必须在自我保全与情感承担之间做出抉择，最终结局让人物完成真正意义上的成长或崩塌。"
                }
            ]
        }

    if step_key == "plot_details":
        return {
            "question": base_question,
            "options": [
                {
                    "label": "三次转折加强",
                    "value": "关键情节一：主角发现看似偶然的事件背后有明确操控痕迹。\n关键情节二：主角最信任的人突然站到对立面，迫使其独自承担后果。\n关键情节三：在高潮前夕，主角意识到自己一直追逐的答案本身就是陷阱。"
                },
                {
                    "label": "秘密逐层揭开",
                    "value": "前段通过一条不起眼的线索埋下秘密。\n中段让主角不断接近真相，但每次接近都会付出更大代价。\n高潮前把秘密与主角个人创伤绑定，使终极冲突既是外部危机，也是内部清算。"
                },
                {
                    "label": "关系驱动冲突",
                    "value": "先让主角与关键角色形成暂时联盟，再通过利益冲突与价值观分歧打破联盟。\n中段加入一次误判导致的严重后果。\n结尾处让人物必须在情感、责任和生存之间做出不可逆选择。"
                }
            ]
        }

    return {
        "question": base_question,
        "options": [
            {"label": "保守推进", "value": "沿着当前设定继续深化，保持逻辑稳定与情绪连贯。"},
            {"label": "冲突升级", "value": "在现有设定基础上提高人物代价与故事张力，让核心冲突更尖锐。"},
            {"label": "反常规处理", "value": "保留故事核心，但加入更出人意料的设定转向或人物选择。"}
        ]
    }


def _strip_markdown_code_fence(text: str) -> str:
    cleaned = str(text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def _remove_json_trailing_commas(text: str) -> str:
    return re.sub(r",(\s*[}\]])", r"\1", text)


def _iter_json_candidates(text: str) -> list[str]:
    cleaned = _strip_markdown_code_fence(text)
    if not cleaned:
        return []

    candidates: list[str] = [cleaned]
    for pattern in (r"\{[\s\S]*\}", r"\[[\s\S]*\]"):
        match = re.search(pattern, cleaned)
        if match:
            candidate = match.group(0).strip()
            if candidate and candidate not in candidates:
                candidates.append(candidate)

    return candidates


def _load_json_payload(text: str):
    for candidate in _iter_json_candidates(text):
        for normalized in (candidate, _remove_json_trailing_commas(candidate)):
            try:
                parsed = json.loads(normalized)
            except json.JSONDecodeError:
                continue

            if isinstance(parsed, dict):
                return parsed

            if isinstance(parsed, list):
                for item in parsed:
                    if isinstance(item, dict):
                        return item

            if isinstance(parsed, str):
                nested = parsed.strip()
                if nested and nested != normalized:
                    nested_payload = _load_json_payload(nested)
                    if nested_payload is not None:
                        return nested_payload

    return None


class InteractionGenerationError(Exception):
    def __init__(self, message: str, *, raw_content: str = "", error_type: str = ""):
        super().__init__(message)
        self.raw_content = str(raw_content or "")
        self.error_type = str(error_type or "interaction_generation_error")


def _option_text(option: dict) -> str:
    label = str(option.get("label", "") or "").strip()
    value = str(option.get("value", "") or "").strip()
    return f"{label}\n{value}".strip()


def _is_relevant_interaction_option(step_key: str, option: dict) -> bool:
    text = _option_text(option)
    if not text:
        return False

    if step_key == "character_details":
        return (
            len(text) >= 20
            and any(keyword in text for keyword in ("主角", "角色", "配角", "反派", "人物", "身份", "关系", "秘密", "目标"))
            and not any(keyword in text for keyword in ("叙事风格", "实验风格", "镜头语言"))
        )

    if step_key == "story_expansion":
        return len(text) >= 40 and any(keyword in text for keyword in ("第一幕", "第二幕", "第三幕", "开端", "中段", "高潮"))

    if step_key == "plot_details":
        return len(text) >= 20 and any(keyword in text for keyword in ("关键", "转折", "冲突", "危机", "真相", "高潮", "结局"))

    return True


def _normalize_interaction_payload(step_key: str, base_question: str, payload: dict, *, strict: bool = False):
    if not isinstance(payload, dict):
        if strict:
            raise InteractionGenerationError(
                f"Interaction payload for step '{step_key}' is not a JSON object",
                error_type="invalid_payload_shape"
            )
        return _build_interaction_fallback(step_key, base_question)

    question = str(payload.get("question", "") or "").strip() or base_question
    raw_options = payload.get("options")
    if not isinstance(raw_options, list):
        if strict:
            raise InteractionGenerationError(
                f"Interaction payload for step '{step_key}' is missing options list",
                error_type="invalid_options_shape"
            )
        return _build_interaction_fallback(step_key, question)

    options = []
    for option in raw_options:
        if not isinstance(option, dict):
            continue
        label = str(option.get("label", "") or "").strip()
        value = str(option.get("value", "") or "").strip()
        if not label or not value:
            continue
        normalized_option = {"label": label, "value": value}
        if _is_relevant_interaction_option(step_key, normalized_option):
            options.append(normalized_option)

    if len(options) < 3:
        if strict:
            raise InteractionGenerationError(
                f"Interaction payload for step '{step_key}' has insufficient valid options",
                error_type="insufficient_valid_options"
            )
        return _build_interaction_fallback(step_key, question)

    return {"question": question, "options": options[:4]}


def _estimate_token_usage(messages, content: str) -> int:
    """
    Estimate token usage when upstream provider does not return usage.
    Chinese chars are roughly 1 token, other chars roughly 1 token per 4 chars.
    """
    try:
        prompt_text = "\n".join(str((msg or {}).get("content", "") or "") for msg in (messages or []))
        completion_text = str(content or "")
        merged = f"{prompt_text}\n{completion_text}"
        cjk_chars = len(re.findall(r"[\u4e00-\u9fff]", merged))
        other_chars = max(0, len(merged) - cjk_chars)
        estimated = int(cjk_chars * 1.05 + other_chars / 4 + max(1, len(messages or [])) * 6)
        return max(1, estimated)
    except Exception:
        return max(1, len(str(content or "")) // 4)

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(Exception)
)
async def raw_generation(messages, temperature=0.7, json_response=False, task_type="default"):
    """
    Generic wrapper for LLM calls with Concurrency Control and Retries.
    Returns (content, usage_count).
    """
    runtime_configs = get_routed_llm_configs(task_type)
    last_error = None
    for runtime_config in runtime_configs:
        try:
            runtime_client = _get_client(runtime_config)
            semaphore = _get_semaphore(runtime_config.max_concurrency)
            async with semaphore:
                logger.info(
                    "LLM调用: 档案=%s 任务=%s 消息数=%s",
                    runtime_config.profile_name,
                    task_type,
                    len(messages),
                )
                response = await runtime_client.chat.completions.create(
                    model=runtime_config.model_id,
                    messages=messages,
                    temperature=temperature
                )
                content = response.choices[0].message.content
                usage_obj = getattr(response, "usage", None)
                usage = int(getattr(usage_obj, "total_tokens", 0) or 0)
                if usage <= 0:
                    usage = _estimate_token_usage(messages, content)
                    logger.info(f"LLM调用: 成功完成 (上游未返回Token，使用估算值: {usage})")
                else:
                    logger.info(f"LLM调用: 成功完成 (消耗Token: {usage})")

                if json_response and content:
                    content = content.replace("```json", "").replace("```", "").strip()
                    json_match = re.search(r'\{.*\}', content, re.DOTALL)
                    if json_match:
                        content = json_match.group(0)
                return content, usage
        except Exception as e:
            last_error = e
            logger.warning(
                "LLM 档案 %s 调用失败，尝试下一候选档案: %s",
                runtime_config.profile_name,
                type(e).__name__,
            )
            if "401" in str(e):
                logger.error("💡 提示: 401 错误通常意味着 API Key 无效或过期，请检查后台 AI 配置。")
            elif "404" in str(e):
                logger.error(
                    "💡 提示: 404 错误通常意味着 Base URL (%s) 不正确或模型 ID (%s) 错误。",
                    runtime_config.base_url,
                    runtime_config.model_id,
                )
    if last_error:
        raise last_error
    raise RuntimeError("没有可用的 AI 配置档案")

async def review_user_input(text: str, template_instructions: str = ""):
    """
    Review user input for policy risks and produce a safe rewrite suggestion.
    Returns:
    {
        "flagged": bool,
        "categories": [str],
        "reason": str,
        "suggested_rewrite": str
    }
    """
    raw_text = (text or "").strip()
    if not raw_text:
        return {
            "flagged": False,
            "categories": [],
            "reason": "",
            "suggested_rewrite": ""
        }

    categories = []
    if _contains_any_pattern(raw_text, _PORN_PATTERNS):
        categories.append("色情信息")
    if _contains_any_pattern(raw_text, _HARMFUL_VALUE_PATTERNS):
        categories.append("价值观风险")

    # Fast path for clearly safe text to avoid extra latency and token cost.
    if not categories:
        return {
            "flagged": False,
            "categories": [],
            "reason": "",
            "suggested_rewrite": ""
        }

    system_prompt = """
    你是内容审核与改写助手。请判断用户文本是否包含：
    1) 色情、露骨性暗示
    2) 极端、歧视、仇恨、鼓吹违法暴力等价值观风险

    如果存在风险，请给出保持创意核心但健康合规的改写版本。
    必须返回 JSON，且只有 JSON：
    {
      "flagged": true,
      "categories": ["色情信息" 或 "价值观风险" 的数组],
      "reason": "一句简短说明",
      "suggested_rewrite": "改写后的文本"
    }
    """
    system_prompt += (
        "\n项目自定义审核要求："
        + (template_instructions or "使用默认审核规则")
    )
    user_prompt = f"待审核文本：\n{raw_text}"

    try:
        content, _ = await raw_generation(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.2,
            json_response=True,
            task_type="review",
        )
        if content:
            parsed = json.loads(content)
            llm_flagged = bool(parsed.get("flagged", False))
            llm_categories = parsed.get("categories") or categories
            if isinstance(llm_categories, str):
                llm_categories = [llm_categories]
            llm_reason = str(parsed.get("reason", "") or "").strip()
            llm_suggestion = str(parsed.get("suggested_rewrite", "") or "").strip()

            # Be conservative: if rule-based checks flagged it, keep flagged=true.
            flagged = llm_flagged or bool(categories)
            final_categories = list(dict.fromkeys((categories or []) + (llm_categories or [])))

            if not llm_suggestion:
                llm_suggestion = _fallback_rewrite(raw_text, final_categories)

            return {
                "flagged": flagged,
                "categories": final_categories,
                "reason": llm_reason or "检测到潜在不当内容，建议改写为健康合规表达。",
                "suggested_rewrite": llm_suggestion
            }
    except Exception as e:
        logger.warning(f"review_user_input fallback due to LLM error: {e}")

    return {
        "flagged": True,
        "categories": categories,
        "reason": "检测到潜在不当内容，建议改写为健康合规表达。",
        "suggested_rewrite": _fallback_rewrite(raw_text, categories)
    }

async def generate_story_synopsis(logline: str, context: dict | None = None, project_type: str = "movie"):
    """
    Generate AI-written brief and detailed synopses from the current project setup.
    Returns ({brief, detailed}, usage)
    """
    clean_logline = (logline or "").strip()
    context = context or {}
    if not clean_logline and not context:
        return {"brief": "", "detailed": ""}, 0

    type_label = {
        "movie": "电影剧本",
        "tv": "剧集剧本",
        "short": "短剧剧本",
        "short_video": "短视频"
    }.get(project_type, "剧本")

    system_prompt = """
    你是专业编剧策划，请根据故事设定输出两版中文梗概。

    要求：
    1. 只返回 JSON，不要输出任何额外说明。
    2. brief：120-180 字，必须是重新组织后的简要梗概，不能直接照抄用户输入的一句话。
    3. detailed：300-500 字，交代主角目标、核心冲突、关键转折和整体走向。
    4. 全部使用简体中文，语言凝练、具有影视策划感。

    返回格式：
    {
      "brief": "简要梗概",
      "detailed": "详细梗概"
    }
    """

    user_prompt = f"""
    项目类型：{type_label}
    故事原始设想：{clean_logline}
    当前设定：{json.dumps(context, ensure_ascii=False)}

    请基于以上信息生成两个版本的故事梗概。
    """

    content, usage = await raw_generation(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        temperature=0.7,
        json_response=True,
        task_type="planning",
    )

    if not content:
        return {"brief": "", "detailed": ""}, usage

    try:
        parsed = json.loads(content)
        return {
            "brief": str(parsed.get("brief", "") or "").strip(),
            "detailed": str(parsed.get("detailed", "") or "").strip()
        }, usage
    except Exception:
        logger.warning("generate_story_synopsis failed to parse JSON response")
        return {"brief": "", "detailed": ""}, usage


async def extract_setup_from_long_input(long_input: str):
    """
    Extract high-confidence setup fields from a long user story description.
    Returns:
    {
        "project_type": "movie|tv|short|short_video|",
        "movie_duration": str,
        "scene_count_target": str,
        "episode_count": str,
        "episode_duration": str,
        "video_duration_seconds": str,
        "tone": str,
        "time_period": str,
        "title": str,
        "story_expansion": str,
        "character_details": str,
        "plot_details": str,
        "theme": str,
        "visual_style": str,
        "user_notes": str
    }, usage
    """
    clean_input = (long_input or "").strip()
    if not clean_input:
        return {}, 0

    system_prompt = """
    你是剧本策划助理。请从用户给出的长篇故事设定中，提取已经明确写出的项目设定。

    规则：
    1. 只返回 JSON，不要输出任何额外说明。
    2. 只填写高置信、文本中已经明确出现的信息；不明确的字段必须留空字符串。
    3. 不要为了凑字段而编造设定。
    4. project_type 只能是 movie、tv、short、short_video 或空字符串。
    5. title 如果文本里有书名号标题，就只提取书名号里的标题；没有就留空。
    6. story_expansion、character_details、plot_details 只有在文本已经提供了较完整信息时才填写，否则留空。

    返回格式：
    {
      "project_type": "",
      "movie_duration": "",
      "scene_count_target": "",
      "episode_count": "",
      "episode_duration": "",
      "video_duration_seconds": "",
      "tone": "",
      "time_period": "",
      "title": "",
      "story_expansion": "",
      "character_details": "",
      "plot_details": "",
      "theme": "",
      "visual_style": "",
      "user_notes": ""
    }
    """

    user_prompt = f"待提取文本：\n{clean_input}"

    content, usage = await raw_generation(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        temperature=0.2,
        json_response=True,
        task_type="planning",
    )

    if not content:
        raise ValueError("Empty extraction payload for long input")

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        logger.warning(f"extract_setup_from_long_input JSON parse failed: {content[:500]}")
        raise ValueError("Invalid JSON extraction payload for long input") from exc

    return parsed, usage

async def analyze_script_requirements(logline: str, project_type: str="movie"):
    """
    Step 1: Analyze logline and ask user for direction.
    """
    type_context = "电影"
    if project_type == "tv": type_context = "电视剧"
    if project_type == "short": type_context = "现代短剧"
    if project_type == "short_video": type_context = "短视频"

    system_prompt = f"""
    You are an expert Script Development Executive ({type_context} expert).
    Analyze the user's logline. Identify the most critical ambiguity or direction choice needed to develop this into a full {type_context} script.
    
    IMPORTANT: You must reply in Chinese (Simplified). The JSON values must be in Chinese.
    
    Return ONLY a JSON object with this structure:
    {{
        "question": "A specific, thought-provoking question about the protagonist's dilemma, tone, or setting.",
        "options": [
            {{"label": "Detailed option description 1", "value": "style_A"}},
            {{"label": "Detailed option description 2", "value": "style_B"}},
            {{"label": "Detailed option description 3", "value": "style_C"}}
        ]
    }}
    Always include 3 distinct creative directions.
    """
    
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Logline: {logline}"}
    ]
    
    content, usage = await raw_generation(
        messages,
        temperature=0.7,
        json_response=True,
        task_type="interaction",
    )
    if content:
        try:
            return json.loads(content), usage
        except:
             logger.error("Failed to parse JSON")
             return None, usage
    return None, 0

async def generate_scene_batch(
    logline: str,
    style_guide: str,
    start_idx: int,
    end_idx: int,
    previous_context: str = "",
    total_target: int = 0,
    template_instructions: str = "",
):
    """
    Generate a specific batch of scenes.
    """
    count = end_idx - start_idx + 1
    system_prompt = f"""
    You are a professional Screenwriter.
    Create a scene-by-scene outline for items #{start_idx} to #{end_idx}.
    Total Items in Project: {total_target}.
    This Batch: {count} items.
    
    Context: {logline}
    Style/Settings: {style_guide}
    Story Bible, Timeline and Continuity State:
    {previous_context}
    
    IMPORTANT: Output in Chinese (Simplified).
    CONTINUITY IS MANDATORY:
    - Item #{start_idx} is an exact absolute scene number, never restart numbering at 1.
    - If #{start_idx} is beyond the opening, continue the established plot immediately.
    - Never repeat the inciting incident or reintroduce established characters as strangers.
    - Respect all character knowledge, relationships, injuries, locations, props and open threads.
    - Advance the story according to the stated progress and narrative phase.
    Project-specific workflow instructions:
    {template_instructions or 'Use the default professional screenwriting workflow.'}
    Return ONLY a JSON object:
    {{
        "scenes": [
            {{"index": {start_idx}, "outline": "..."}},
            ...
            {{"index": {end_idx}, "outline": "..."}}
        ]
    }}
    """
    messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": "Generate scenes."}]
    content, usage = await raw_generation(
        messages,
        temperature=0.7,
        json_response=True,
        task_type="outline",
    )
    if content:
        try:
            import re
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match: content = json_match.group(0)
            data = json.loads(content)
            return data.get("scenes", []), usage
        except Exception as e:
            logger.error(f"Batch {start_idx}-{end_idx} JSON Error: {e}")
    return [], usage

async def write_scene_content(
    logline: str,
    style_guide: str,
    current_scene_outline: str,
    previous_context: str = "",
    scene_index: int = 1,
    total_scenes: int = 1,
    template_instructions: str = "",
):
    """
    Step 3: Write the actual script for a scene. Returns (content, usage).
    """
    system_prompt = f"""
    You are an AI Screenwriting Engine. Write a full scene script in standard screenplay format.
    
    Project Logline: {logline}
    Style: {style_guide}
    
    Current Position: Scene {scene_index} of {total_scenes}.

    Story Bible, Timeline and Continuity State:
    {previous_context}
    
    Current Scene Goal:
    {current_scene_outline}
    
    Instructions:
    - Write in professional Screenplay format.
    - Be concise but dramatic.
    - This is Scene {scene_index}, NOT Scene 1. Continue directly from the previous ending.
    - Do not repeat the opening, inciting incident, character introductions or already resolved events.
    - Preserve character state, knowledge, relationships, injuries, props, locations and unresolved threads.
    - Make the ending state explicit enough for Scene {scene_index + 1} to continue.
    - Apply these project-specific workflow instructions: {template_instructions or 'default screenplay workflow'}
    - IMPORTANT: Write mainly in Chinese (Dialogues and Actions).
    - TRANSLATE SCENE HEADERS: Convert 'INT.' to '内景', 'EXT.' to '外景', 'DAY' to '日', 'NIGHT' to '夜'.
    - FORCE: The output language MUST be Chinese (Simplified) for everything including Headers, Transitions, Dialogue and Actions.
    - Output ONLY the raw text.
    """
    
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "Action! Write in Chinese."}
    ]
    
    return await raw_generation(messages, temperature=0.8, task_type="content")


async def write_short_video_prompt(
    logline: str,
    style_guide: str,
    current_scene_outline: str,
    clip_index: int,
    previous_context: str = "",
    template_instructions: str = "",
):
    """
    Generate a single 15-second short-video prompt block.
    Returns (content, usage).
    """
    system_prompt = f"""
    你是短视频分镜提示词专家。请基于剧情信息，输出“第{clip_index}条 15秒提示词”。

    项目核心设定：
    - Logline: {logline}
    - 风格与设定: {style_guide}
    - 前文衔接: {previous_context}
    - 本条剧情目标: {current_scene_outline}
    - 项目工作流要求: {template_instructions or '默认短视频工作流'}

    运镜术语库（优先从下列术语中选择并组合）：
    - 推镜头 / 慢推
    - 拉镜头 / 后拉
    - 左摇 / 右摇
    - 上摇 / 下摇
    - 跟随镜头 / 跟拍
    - 环绕镜头
    - 一镜到底
    - 希区柯克变焦
    - 鱼眼镜头
    - 低角度仰拍
    - 俯拍 / 鸟瞰
    - 第一人称主观视角
    - 快速摇镜
    - 机械臂跟随
    - 极致特写
    - 面部特写
    - 中近景
    - 中景
    - 全景
    - 远景 / 建立镜头

    输出要求：
    1. 必须使用简体中文。
    2. 只输出正文，不要解释、不要 Markdown 代码块。
    3. 严格按以下结构输出，每个字段都要有内容：
    [主体/人物设定]：
    [场景/环境]：
    [动作/运动描述]：
    [运镜语言]：
    [分时段描述]：
    0–3秒：[开场画面描述、运镜、动作]
    3–6秒：[中段发展]
    6–10秒：[高潮或关键动作]
    10–15秒：[收尾、定格画面、品牌文字]
    [转场/特效]：
    [音频/音效设计]：
    [风格/氛围]：

    4. 内容必须具有连续剧情推进，避免与前一条完全重复。
    """

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "请生成这一条15秒短视频提示词。"}
    ]

    return await raw_generation(messages, temperature=0.7, task_type="content")

async def rewrite_scene_to_ai_prompt(
    project_type: str,
    logline: str,
    style_guide: str,
    scene_outline: str,
    scene_content: str,
    scene_index: int,
    template_instructions: str = "",
):
    """
    Rewrite an existing scene into an AI-generation friendly prompt block.
    Returns (prompt_text, usage).
    """
    type_label = {
        "movie": "电影剧本",
        "tv": "剧集剧本",
        "short": "短剧剧本",
        "short_video": "短视频剧本",
    }.get(project_type, "剧本")

    if project_type == "short_video":
        timeline_instruction = """
    分时段描述请严格使用：
    0–3秒：[开场画面描述、运镜、动作]
    3–6秒：[中段发展]
    6–10秒：[高潮或关键动作]
    10–15秒：[收尾、定格画面、品牌文字]
        """
    else:
        timeline_instruction = """
    分时段描述请严格使用四段节奏：
    第一段：[开场画面与角色状态]
    第二段：[冲突推进与动作变化]
    第三段：[高潮/反转与视觉爆点]
    第四段：[收束画面与情绪落点]
        """

    system_prompt = f"""
    你是影视 AIGC 提示词导演，请把现有场景转写成可直接用于视频生成模型的中文提示词。
    项目类型：{type_label}

    输出要求（必须全部满足）：
    1. 只输出纯文本，不要 JSON，不要 Markdown 代码块，不要额外解释。
    2. 必须包含以下字段，并按顺序逐行输出：
       [主体/人物设定]：
       [场景/环境]：
       [动作/运动描述]：
       [运镜语言]：
       [分时段描述]：
       [转场/特效]：
       [音频/音效设计]：
       [风格/氛围]：
    3. 运镜语言必须使用专业术语（推镜头/拉镜头/跟拍/环绕/俯拍/仰拍/特写等）。
    4. 描述要具体可执行，避免空泛词。
    {timeline_instruction}
    项目自定义提示词要求：{template_instructions or '使用默认提示词转写规则'}
    """

    user_prompt = f"""
    项目 logline：
    {logline or "无"}

    当前风格设定：
    {style_guide or "无"}

    场次编号：第{scene_index}场
    场次大纲：
    {scene_outline or "无"}

    场次正文：
    {scene_content or "无"}
    """

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    return await raw_generation(messages, temperature=0.4, task_type="prompt")

async def generate_interaction_options(
    step_key: str,
    base_question: str,
    context_str: str,
    template_instructions: str = "",
):
    """
    Generates tailored options for a specific step in the Project Bible creation.
    Follows "Snowflake Method" principles (Iterative Expansion).
    """
    system_prompt = """
    You are a professional Script Consultant and Story Architect. 
    Your goal is to guide the user in defining their story's "Bible" using the Snowflake Method (雪花写作法).
    
    Current Task: Based on the current story context, generate 3-4 creative and distinct options for a specific aspect of the story.
    
    CRITICAL INSTRUCTION - SNOWFLAKE METHOD:
    - If the user has already provided some details, DON'T ask basic questions. Instead, propose EXPANSIONS or CONFLICTS that build on what they have.
    - Focus on deepening the stakes, clarifying character motivations, or expanding the world-building.
    - If the 'Target Field' is 'story_expansion', provide three different 3-act structure summaries.
    - If the 'Target Field' is 'character_details', suggest specific character arcs or hidden secrets.
    
    IMPORTANT: The entire output MUST be in Chinese (Simplified).
    RETURN A JSON OBJECT ONLY.
    DO NOT wrap the JSON in markdown fences.
    DO NOT add any explanation before or after the JSON.
    USE double quotes for all keys and string values.
    
    Output Format (JSON):
    {
        "question": "The refined, thought-provoking question (In Chinese)",
        "options": [
            {"label": "Detailed option description (User visible)", "value": "The actual content to be saved to the project bible (Must be detailed)"}
        ]
    }
    
    SPECIAL RULE FOR 'TITLE':
    The 'value' must be the TITLE ITSELF.
    
    SPECIAL RULE FOR COMPLEX FIELDS ('character_details', 'story_expansion', 'plot_details', 'world_building'):
    The 'value' MUST be the FULL, DETAILED text of the option, not a summary.
    For 'character_details', the 'value' should list all characters with their traits.
    """
    system_prompt += (
        "\nProject-specific workflow instructions: "
        + (template_instructions or "default interaction workflow")
    )
    
    user_prompt = f"""
    Context:
    {context_str}
    
    Target Field: {step_key}
    Standard Question: {base_question}
    
    Generate options that fit the genre and logic of the logline. 
    Ensure options allow for variety (e.g., one safe, one subversive, one high-concept).
    REPLY IN CHINESE ONLY. ENSURE 'value' fields contain the FULL CONTENT.
    OUTPUT JSON ONLY. NO markdown fences. NO explanatory text.
    """
    
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]
    
    if step_key == 'character_details':
        # Append to the *user* prompt or system prompt effectively
        # But here we append string to messages list construction logic
        # Actually in original code it appended to system_prompt *after* messages list was built? 
        # No, look at original code:
        # messages = [...]
        # if step_key == ...: system_prompt += ...
        # This was a bug in original code! system_prompt string modification AFTER list creation does nothing!
        pass 

    # Re-construct messages to ensure system prompt includes specific instructions
    if step_key == 'character_details':
        system_prompt += "\n\nCRITICAL: For 'character_details', offer options that list the FULL Main Cast (Protagonist, Antagonist, Supporting) with 1-line bios for each. Format as a structured list."
    
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]

    content, usage = await raw_generation(
        messages,
        temperature=0.3,
        json_response=True,
        task_type="interaction",
    )
    if not content:
        raise InteractionGenerationError(
            f"Empty interaction payload for step: {step_key}",
            error_type="empty_payload"
        )

    parsed = _load_json_payload(content)
    if parsed is None:
        logger.warning(f"generate_interaction_options JSON parse failed for {step_key}: {content[:500]}")
        raise InteractionGenerationError(
            f"Invalid JSON interaction payload for step: {step_key}",
            raw_content=content,
            error_type="json_parse_failed"
        )

    try:
        normalized_payload = _normalize_interaction_payload(step_key, base_question, parsed, strict=True)
    except InteractionGenerationError as exc:
        if not exc.raw_content:
            exc.raw_content = content
        raise

    return normalized_payload, usage
