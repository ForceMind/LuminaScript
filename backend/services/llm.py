from openai import AsyncOpenAI
import os
import json
import logging
import re
from pathlib import Path

import asyncio
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

# Configure Configuration
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

# Load Config
API_KEY = os.getenv("LLM_API_KEY")
BASE_URL = os.getenv("LLM_BASE_URL", "https://maas-api.cn-huabei-1.xf-yun.com/v1")
MODEL_ID = os.getenv("LLM_MODEL_ID", "xopglm47blth2")

if not API_KEY:
    logger.warning("⚠️ LLM_API_KEY implies not set. LLM features will fail. Please set it in .env file.")
else:
    masked_key = API_KEY[:4] + "****" + API_KEY[-4:] if len(API_KEY) > 8 else "****"
    logger.info(f"LLM 服务配置加载: Model={MODEL_ID}, BaseURL={BASE_URL}, Key={masked_key}")

client = AsyncOpenAI(
    api_key=API_KEY if API_KEY else "dummy_key", # Prevent client init failure, fail at request time
    base_url=BASE_URL,
)

# Semantic Semaphore to limit concurrency globally (Max 20 concurrent requests)
# We initialize it lazily or at module level if we are in an event loop, 
# but safely we can use a bounded semaphore.
_sem = asyncio.Semaphore(20)

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

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(Exception)
)
async def raw_generation(messages, temperature=0.7, json_response=False):
    """
    Generic wrapper for LLM calls with Concurrency Control and Retries.
    Returns (content, usage_count).
    """
    async with _sem:
        try:
            logger.info(f"LLM调用: 开始生成... (消息数: {len(messages)})")
            # Note: Removing response_format as some providers (like current Xunfei gateway) do not support it
            # We use extra_body={"response_format": ...} only if supported, but here currently disabled for stability
            response = await client.chat.completions.create(
                model=MODEL_ID,
                messages=messages,
                temperature=temperature
            )
            content = response.choices[0].message.content
            usage = response.usage.total_tokens if response.usage else 0
            
            logger.info(f"LLM调用: 成功完成 (消耗Token: {usage})")
            
            # If user expects JSON, we try to clean it up lightly
            if json_response and content:
                 content = content.replace("```json", "").replace("```", "").strip()
                 # Try to find the first '{' and last '}' to extract valid JSON
                 import re
                 json_match = re.search(r'\{.*\}', content, re.DOTALL)
                 if json_match:
                     content = json_match.group(0)
            
            return content, usage
        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            logger.error(f"❌ LLM调用失败 Details:\nERROR_TYPE: {type(e).__name__}\nMESSAGE: {str(e)}\nTRACE:\n{error_details}")
            
            # Additional debug info for specific failures
            if "401" in str(e):
                logger.error("💡 提示: 401 错误通常意味着 API Key 无效或过期。请检查 .env 文件。")
            elif "404" in str(e):
                logger.error(f"💡 提示: 404 错误通常意味着 Base URL ({BASE_URL}) 不正确或模型 ID ({MODEL_ID}) 错误。")
            
            raise e # Raise to trigger retry

async def review_user_input(text: str):
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
    user_prompt = f"待审核文本：\n{raw_text}"

    try:
        content, _ = await raw_generation(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.2,
            json_response=True
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
    Returns:
    {
        "brief": str,
        "detailed": str
    }
    """
    clean_logline = (logline or "").strip()
    context = context or {}
    if not clean_logline and not context:
        return {"brief": "", "detailed": ""}

    type_label = {
        "movie": "电影剧本",
        "tv": "剧集剧本",
        "short": "短剧剧本"
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

    content, _ = await raw_generation(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        temperature=0.7,
        json_response=True
    )

    if not content:
        return {"brief": "", "detailed": ""}

    try:
        parsed = json.loads(content)
        return {
            "brief": str(parsed.get("brief", "") or "").strip(),
            "detailed": str(parsed.get("detailed", "") or "").strip()
        }
    except Exception:
        logger.warning("generate_story_synopsis failed to parse JSON response")
        return {"brief": "", "detailed": ""}

async def analyze_script_requirements(logline: str, project_type: str="movie"):
    """
    Step 1: Analyze logline and ask user for direction.
    """
    type_context = "电影"
    if project_type == "tv": type_context = "电视剧"
    if project_type == "short": type_context = "现代短剧"

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
    
    content, usage = await raw_generation(messages, temperature=0.7, json_response=True)
    if content:
        try:
            return json.loads(content), usage
        except:
             logger.error("Failed to parse JSON")
             return None, usage
    return None, 0

async def generate_scene_batch(logline: str, style_guide: str, start_idx: int, end_idx: int, previous_context: str = "", total_target: int = 0):
    """
    Generate a specific batch of scenes.
    """
    count = end_idx - start_idx + 1
    system_prompt = f"""
    You are a professional Screenwriter.
    Create a scene-by-scene outline for scenes #{start_idx} to #{end_idx}.
    Total Scenes in Movie: {total_target}.
    This Batch: {count} scenes.
    
    Context: {logline}
    Style/Settings: {style_guide}
    Previous Scene Arc: {previous_context}
    
    IMPORTANT: Output in Chinese (Simplified).
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
    content, usage = await raw_generation(messages, temperature=0.7, json_response=True)
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

async def write_scene_content(logline: str, style_guide: str, current_scene_outline: str, previous_context: str = ""):
    """
    Step 3: Write the actual script for a scene. Returns (content, usage).
    """
    system_prompt = f"""
    You are an AI Screenwriting Engine. Write a full scene script in standard screenplay format.
    
    Project Logline: {logline}
    Style: {style_guide}
    
    Context from previous scenes:
    {previous_context}
    
    Current Scene Goal:
    {current_scene_outline}
    
    Instructions:
    - Write in professional Screenplay format.
    - Be concise but dramatic.
    - IMPORTANT: Write mainly in Chinese (Dialogues and Actions).
    - TRANSLATE SCENE HEADERS: Convert 'INT.' to '内景', 'EXT.' to '外景', 'DAY' to '日', 'NIGHT' to '夜'.
    - FORCE: The output language MUST be Chinese (Simplified) for everything including Headers, Transitions, Dialogue and Actions.
    - Output ONLY the raw text.
    """
    
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "Action! Write in Chinese."}
    ]
    
    return await raw_generation(messages, temperature=0.8)

async def generate_interaction_options(step_key: str, base_question: str, context_str: str):
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
    
    user_prompt = f"""
    Context:
    {context_str}
    
    Target Field: {step_key}
    Standard Question: {base_question}
    
    Generate options that fit the genre and logic of the logline. 
    Ensure options allow for variety (e.g., one safe, one subversive, one high-concept).
    REPLY IN CHINESE ONLY. ENSURE 'value' fields contain the FULL CONTENT.
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

    content, usage = await raw_generation(messages, temperature=0.8, json_response=True)
    if content:
        try:
            return json.loads(content), usage
        except:
            pass
            
    # Fallback
    return {
        "question": base_question,
        "options": [
            {"label": "经典模式", "value": "经典叙事风格"},
            {"label": "反转模式", "value": "带有反转的剧情"},
            {"label": "实验风格", "value": "大胆的实验性风格"}
        ]
    }, usage
