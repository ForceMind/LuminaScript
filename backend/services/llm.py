from openai import AsyncOpenAI
import os
import json
import logging

import asyncio
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

# Configure Configuration
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
                 # Heuristic fix for trailing comma or simple markdown wrapper
            
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

async def generate_outline(logline: str, style_guide: str):
    """
    Step 2: Generate a list of scenes. Matches return signature (data, usage).
    """
    logger.info("Step 2: 正在生成分场大纲...")
    system_prompt = """
    You are a professional Screenwriter.
    Based on the logline and selected style, create a concise scene-by-scene outline.
    Create exactly 5 scenes for this demo.
    
    IMPORTANT: Output in Chinese (Simplified).
    
    Return ONLY a JSON object:
    {
        "scenes": [
            {"index": 1, "outline": "具体的场景梗概 (Description)..."},
            {"index": 2, "outline": "..."}
        ]
    }
    """
    
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Logline: {logline}\nStyle Direction: {style_guide}"}
    ]
    
    content, usage = await raw_generation(messages, temperature=0.7, json_response=True)
    if content:
        try:
            data = json.loads(content)
            return data.get("scenes", []), usage
        except json.JSONDecodeError:
            logger.error(f"Generate Outline JSON Decode Error. Content: {content}")
            return [], usage
    return [], 0

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
    - IMPORTANT: Write mainly in Chinese (Dialogues and Actions), but standard format markers (INT./EXT.) can be standard.
    - Output ONLY the raw text.
    """
    
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "Action!"}
    ]
    
    return await raw_generation(messages, temperature=0.8)

async def generate_interaction_options(step_key: str, base_question: str, context_str: str):
    """
    Generates tailored options for a specific step in the Project Bible creation.
    Returns: {"question": "Refined Question?", "options": [{"label": "...", "value": "..."}, ...]}
    """
    system_prompt = """
    You are a professional Script Consultant (Script Doctor). 
    Your goal is to guide the user in defining their story's "Bible" step-by-step.
    
    Current Task: Generate 3-4 creative, distinct options for a specific aspect of the story based on the Logline an Context.
    
    CRITICAL INSTRUCTION - ADAPTIVITY:
    - Assess the available context. If the user has ALREADY provided detailed information about this specific aspect in their Logline or previous answers, provide options that *refine* or *challenge* that detail, rather than asking basic questions.
    - If the context is sparse, provide broad, inspiring options.
    
    IMPORTANT: The entire output MUST be in Chinese (Simplified). The question and all options (labels and values) must be in Chinese.
    
    Output Format (JSON):
    {
        "question": "The refined question to ask the user (In Chinese)",
        "options": [
            {"label": "Option text desc (e.g. '黑暗赛博朋克')", "value": "short_summary_of_option_in_chinese"}
        ]
    }
    """
    
    user_prompt = f"""
    Context:
    {context_str}
    
    Target Field: {step_key}
    Standard Question: {base_question}
    
    Generate options that fit the genre and logic of the logline. 
    Ensure options allow for variety (e.g., one safe, one subversive, one high-concept).
    REPLY IN CHINESE ONLY.
    """
    
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
            {"label": "Standard/Classic Approach", "value": "classic"},
            {"label": "Subversive/Twist Approach", "value": "subversive"},
            {"label": "Experimental Approach", "value": "experimental"}
        ]
    }, usage
