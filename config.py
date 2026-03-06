"""
Configuration module for the Bug Detection Agent system.
Loads settings from environment variables and .env file.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file if it exists
load_dotenv()

# ─── Paths ───────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.resolve()
OUTPUT_CSV_PATH = PROJECT_ROOT / "output.csv"
DEFAULT_INPUT_CSV = PROJECT_ROOT / "input_dataset.csv"
USB_DIR = PROJECT_ROOT / "usb"
MCP_SERVER_DIR = USB_DIR / "server"

# ─── LLM Configuration ──────────────────────────────────────────────────────
# Primary: Groq (fast inference, free tier)
# Fallback: Gemini Flash
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "groq")
LLM_FALLBACK_PROVIDER = os.getenv("LLM_FALLBACK_PROVIDER", "gemini")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3")

# ─── MCP Server Configuration ───────────────────────────────────────────────
MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://localhost:8003/sse")
MCP_TRANSPORT = os.getenv("MCP_TRANSPORT", "sse")
MCP_SERVER_SCRIPT = str(MCP_SERVER_DIR / "mcp_server.py")
MCP_SERVER_PORT = int(os.getenv("MCP_SERVER_PORT", "8003"))

# ─── Detection Settings ─────────────────────────────────────────────────────
DIFF_CONFIDENCE_THRESHOLD = 0.90
PATTERN_CONFIDENCE_THRESHOLD = 0.70
LLM_CONFIDENCE_THRESHOLD = 0.50

ENABLE_DIFF_DETECTION = True
ENABLE_PATTERN_DETECTION = True
ENABLE_LLM_DETECTION = True

# ─── Logging ─────────────────────────────────────────────────────────────────
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
