"""
LLM Client — unified interface for calling LLMs.

Primary: Gemini Flash (free tier) with retry for 429s
Fallback: Groq (fast, free tier)
Also supports: OpenAI, Ollama

Includes automatic retry with exponential backoff for rate-limited APIs,
and provider fallback on failure.
"""

from __future__ import annotations
import asyncio
import json
import logging
from typing import Optional

import config

logger = logging.getLogger(__name__)

# Track request timestamps for throttling
_last_request_time: float = 0.0
_MIN_REQUEST_INTERVAL = 4.0  # seconds between requests (15 RPM = 4s/req)


async def call_llm(
    prompt: str,
    system_prompt: str = "You are an expert C++ bug detection assistant.",
    json_mode: bool = False,
    temperature: float = 0.1,
) -> str:
    """
    Send a prompt to the configured LLM provider with automatic fallback.
    Includes retry logic for rate-limited APIs (429 errors).
    """
    global _last_request_time
    
    # Throttle: ensure minimum time between requests
    import time
    now = time.time()
    elapsed = now - _last_request_time
    if elapsed < _MIN_REQUEST_INTERVAL:
        wait = _MIN_REQUEST_INTERVAL - elapsed
        logger.debug(f"Throttling: waiting {wait:.1f}s between requests")
        await asyncio.sleep(wait)
    _last_request_time = time.time()
    
    primary = config.LLM_PROVIDER.lower()
    fallback = config.LLM_FALLBACK_PROVIDER.lower()
    
    # Try primary provider (with retries for 429)
    try:
        return await _call_with_retry(primary, prompt, system_prompt, json_mode, temperature)
    except Exception as e:
        logger.warning(f"Primary LLM ({primary}) failed: {e}")
    
    # Try fallback provider (with retries for 429)
    if fallback and fallback != primary:
        try:
            logger.info(f"Falling back to: {fallback}")
            return await _call_with_retry(fallback, prompt, system_prompt, json_mode, temperature)
        except Exception as e:
            logger.error(f"Fallback LLM ({fallback}) also failed: {e}")
            raise RuntimeError(
                f"Both LLM providers failed. Primary ({primary}) and Fallback ({fallback})."
            ) from e
    
    raise RuntimeError(f"LLM provider '{primary}' failed and no fallback configured.")


async def _call_with_retry(
    provider: str,
    prompt: str,
    system_prompt: str,
    json_mode: bool,
    temperature: float,
    max_retries: int = 3,
    initial_delay: float = 10.0,
) -> str:
    """Call a provider with exponential backoff retry on 429/rate-limit errors."""
    last_error = None
    
    for attempt in range(max_retries + 1):
        try:
            return await _call_provider(provider, prompt, system_prompt, json_mode, temperature)
        except Exception as e:
            last_error = e
            error_str = str(e).lower()
            
            # Only retry on rate limit / resource exhausted errors
            is_rate_limit = any(kw in error_str for kw in ["429", "rate_limit", "resource_exhausted", "too many"])
            
            if is_rate_limit and attempt < max_retries:
                delay = initial_delay * (2 ** attempt)  # 10s, 20s, 40s
                logger.info(f"Rate limited by {provider}. Retry {attempt+1}/{max_retries} in {delay:.0f}s...")
                await asyncio.sleep(delay)
            else:
                raise
    
    raise last_error  # Should not reach here


async def _call_provider(
    provider: str,
    prompt: str,
    system_prompt: str,
    json_mode: bool,
    temperature: float,
) -> str:
    """Route to the correct provider implementation."""
    if provider == "gemini":
        return await _call_gemini(prompt, system_prompt, json_mode, temperature)
    elif provider == "groq":
        return await _call_groq(prompt, system_prompt, json_mode, temperature)
    elif provider == "openai":
        return await _call_openai(prompt, system_prompt, json_mode, temperature)
    elif provider == "ollama":
        return await _call_ollama(prompt, system_prompt, json_mode, temperature)
    else:
        raise ValueError(f"Unknown LLM provider: {provider}")


# ─── Gemini (Primary) ───────────────────────────────────────────────────────

async def _call_gemini(
    prompt: str,
    system_prompt: str,
    json_mode: bool,
    temperature: float,
) -> str:
    """Call the Google Gemini API (free tier)."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=config.GEMINI_API_KEY)

    generation_config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        temperature=temperature,
        max_output_tokens=2000,
    )

    if json_mode:
        generation_config.response_mime_type = "application/json"

    response = await client.aio.models.generate_content(
        model=config.GEMINI_MODEL,
        contents=prompt,
        config=generation_config,
    )
    content = response.text or ""
    logger.debug(f"Gemini response ({len(content)} chars)")
    return content


# ─── Groq (Fallback) ────────────────────────────────────────────────────────

async def _call_groq(
    prompt: str,
    system_prompt: str,
    json_mode: bool,
    temperature: float,
) -> str:
    """Call the Groq API (fast inference, free tier available)."""
    from groq import AsyncGroq

    client = AsyncGroq(api_key=config.GROQ_API_KEY)

    kwargs: dict = {
        "model": config.GROQ_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
        "max_tokens": 2000,
    }

    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    response = await client.chat.completions.create(**kwargs)
    content = response.choices[0].message.content or ""
    logger.debug(f"Groq response ({len(content)} chars)")
    return content


# ─── OpenAI ──────────────────────────────────────────────────────────────────

async def _call_openai(
    prompt: str,
    system_prompt: str,
    json_mode: bool,
    temperature: float,
) -> str:
    """Call the OpenAI API."""
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=config.OPENAI_API_KEY)

    kwargs: dict = {
        "model": config.OPENAI_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
        "max_tokens": 2000,
    }

    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    response = await client.chat.completions.create(**kwargs)
    content = response.choices[0].message.content or ""
    logger.debug(f"OpenAI response ({len(content)} chars)")
    return content


# ─── Ollama ──────────────────────────────────────────────────────────────────

async def _call_ollama(
    prompt: str,
    system_prompt: str,
    json_mode: bool,
    temperature: float,
) -> str:
    """Call a local Ollama model via its REST API."""
    import aiohttp

    url = f"{config.OLLAMA_BASE_URL.rstrip('/')}/api/generate"

    payload = {
        "model": config.OLLAMA_MODEL,
        "system": system_prompt,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": temperature},
    }

    if json_mode:
        payload["format"] = "json"

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"Ollama returned status {resp.status}: {text}")
            data = await resp.json()
            content = data.get("response", "")
            logger.debug(f"Ollama response ({len(content)} chars)")
            return content


# ─── Utilities ───────────────────────────────────────────────────────────────

def parse_json_response(response: str) -> dict:
    """
    Parse a JSON response from the LLM, handling common formatting issues
    like markdown code block wrappers.
    """
    text = response.strip()

    # Strip markdown code block wrappers
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]

    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse LLM JSON: {e}\nResponse: {text[:500]}")
        raise ValueError(f"LLM returned invalid JSON: {e}") from e
