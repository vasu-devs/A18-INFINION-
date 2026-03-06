"""Quick DeepSeek V3 connectivity test."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import asyncio
import config

async def test():
    print(f"Provider: {config.LLM_PROVIDER}")
    print(f"Model: {config.DEEPSEEK_MODEL}")
    print(f"Base URL: {config.DEEPSEEK_BASE_URL}")
    print(f"Key: {config.DEEPSEEK_API_KEY[:12]}...")
    
    from openai import AsyncOpenAI
    client = AsyncOpenAI(
        api_key=config.DEEPSEEK_API_KEY,
        base_url=config.DEEPSEEK_BASE_URL,
    )
    try:
        resp = await client.chat.completions.create(
            model=config.DEEPSEEK_MODEL,
            messages=[{"role": "user", "content": "Reply OK"}],
            temperature=0.1, max_tokens=10,
        )
        print(f"DEEPSEEK V3 OK: {resp.choices[0].message.content}")
    except Exception as e:
        print(f"DEEPSEEK FAILED: {type(e).__name__}: {e}")

asyncio.run(test())
