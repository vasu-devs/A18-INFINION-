"""Quick test to verify Gemini API connection."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import asyncio
import config

async def test():
    print(f"Gemini key: {config.GEMINI_API_KEY[:10]}...")
    print(f"Groq key: {config.GROQ_API_KEY[:10]}...")
    
    # Test Gemini
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=config.GEMINI_API_KEY)
        cfg = types.GenerateContentConfig(temperature=0.1, max_output_tokens=50)
        resp = await client.aio.models.generate_content(
            model="gemini-2.0-flash",
            contents="Reply with exactly: OK_GEMINI",
            config=cfg,
        )
        print(f"GEMINI OK: {resp.text}")
    except Exception as e:
        print(f"GEMINI FAILED: {type(e).__name__}: {e}")
    
    # Test Groq
    try:
        from groq import AsyncGroq
        client = AsyncGroq(api_key=config.GROQ_API_KEY)
        resp = await client.chat.completions.create(
            model=config.GROQ_MODEL,
            messages=[{"role": "user", "content": "Reply with exactly: OK_GROQ"}],
            temperature=0.1, max_tokens=50,
        )
        print(f"GROQ OK: {resp.choices[0].message.content}")
    except Exception as e:
        print(f"GROQ FAILED: {type(e).__name__}: {e}")

asyncio.run(test())
