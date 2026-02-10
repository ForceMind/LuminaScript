import asyncio
import os
import sys
import json
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger("Distest")

# Load .env manually to ensure we have the latest values
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '.env'))
except ImportError:
    pass

from openai import AsyncOpenAI

# 1. Configuration
API_KEY = os.getenv("LLM_API_KEY")
BASE_URL = os.getenv("LLM_BASE_URL")
MODEL_ID = os.getenv("LLM_MODEL_ID")

print(f"🔹 Configuration:")
print(f"   URL: {BASE_URL}")
print(f"   Model: {MODEL_ID}")
print(f"   Key: {API_KEY[:5]}...{API_KEY[-4:] if API_KEY else ''}")

async def test_connection():
    """
    Test 1: Simple connectivity check
    """
    print("\n🔹 Step 1: Testing Connection...", end=" ")
    
    client = AsyncOpenAI(
        api_key=API_KEY,
        base_url=BASE_URL,
    )
    
    try:
        response = await client.chat.completions.create(
            model=MODEL_ID,
            messages=[{"role": "user", "content": "Hello. 1+1=?"}],
            max_tokens=50
        )
        content = response.choices[0].message.content
        print("✅ Success!")
        print(f"   Response: {content}")
        return client # Return successful client
    except Exception as e:
        print("❌ Failed!")
        print(f"   Error: {e}")
        return None

async def test_main_workflow(client):
    """
    Test 2: Simulate Main Workflow (Analysis -> Outline -> Write)
    """
    print("\n🔹 Step 2: Testing Main Workflow Logic")
    
    logline = "A robot discovers it can dream."
    
    # 2.1 Analysis
    print("\n   [1/3] Testing Analysis (Logline -> Options)...")
    try:
        sys_prompt_analyze = """
        Analyze the logline. Return JSON:
        { "question": "Q?", "options": [{"label": "A", "value": "a"}] }
        """
        # REMOVED response_format for test
        resp = await client.chat.completions.create(
            model=MODEL_ID,
            messages=[
                {"role": "system", "content": sys_prompt_analyze},
                {"role": "user", "content": f"Logline: {logline}"}
            ],
            # response_format={"type": "json_object"}, 
            temperature=0.7
        )
        content_raw = resp.choices[0].message.content
        # Try to parse
        # Sometimes AI adds markdown ```json ... ```
        clean_content = content_raw.replace("```json", "").replace("```", "").strip()
        if not clean_content.endswith("}") and "}" in clean_content:
             # Heuristic fix for cut-off JSON
             clean_content = clean_content[:clean_content.rindex("}")+1]

        data = json.loads(clean_content)
        
        print(f"   ✅ Analysis Result: {data.get('question')} (Options: {len(data.get('options', []))})")
    except Exception as e:
        print(f"   ❌ Analysis Failed: {e}")
        print(f"   Raw Content: {content_raw}")
        return

    # 2.2 Outline
    print("\n   [2/3] Testing Outline (Logline -> Scenes)...")
    try:
        sys_prompt_outline = """
        Create 2 scenes. Return JSON: { "scenes": [{"index":1, "outline":"..."}, {"index":2, "outline":"..."}] }
        """
         # REMOVED response_format for test
        resp = await client.chat.completions.create(
            model=MODEL_ID,
            messages=[
                {"role": "system", "content": sys_prompt_outline},
                {"role": "user", "content": "Generate outline."}
            ]
        )
        content_raw = resp.choices[0].message.content
        clean_content = content_raw.replace("```json", "").replace("```", "").strip()
        data = json.loads(clean_content)
        
        scenes = data.get("scenes", [])
        print(f"   ✅ Outline Result: Generated {len(scenes)} scenes.")
        first_scene_outline = scenes[0]['outline'] if scenes else "Default scene"
    except Exception as e:
        print(f"   ❌ Outline Failed: {e}")
        return

    # 2.3 Scene Writing
    print("\n   [3/3] Testing Writing (Outline -> Script)...")
    try:
        resp = await client.chat.completions.create(
            model=MODEL_ID,
            messages=[
                {"role": "system", "content": "Write a script scene."},
                {"role": "user", "content": f"Scene Goal: {first_scene_outline}"}
            ]
        )
        content = resp.choices[0].message.content
        print(f"   ✅ Writing Result: {len(content)} chars.")
        print(f"   Preview: {content[:100]}...")
    except Exception as e:
        print(f"   ❌ Writing Failed: {e}")

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    # Run the tests
    client = asyncio.run(test_connection())
    if client:
        asyncio.run(test_main_workflow(client))
    else:
        print("\n⚠️ Skipping Main Workflow test due to connection failure.")
