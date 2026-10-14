"""All knobs in one place. Values come from .env / environment, never from code."""
import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent

# --- LLM via OpenRouter (OpenAI-compatible) ---------------------------------
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Cheap model for judging (hundreds of calls), stronger model for summaries (a few).
# Any OpenRouter model id works; change the string, nothing else.
JUDGE_MODEL = os.getenv("JUDGE_MODEL", "anthropic/claude-haiku-5.5")
SUMMARY_MODEL = os.getenv("SUMMARY_MODEL", "anthropic/claude-sonnet-5.5")
# OpenRouter falls back to these if the primary model errors or is rate-limited.
FALLBACK_MODELS = os.getenv("FALLBACK_MODELS", "openai/gpt-5-mini,google/gemini-2.5-flash").split(",")

# --- Hard limits: the agent stops, it does not "try harder" -----------------
MAX_LLM_CALLS_PER_RUN = int(os.getenv("MAX_LLM_CALLS_PER_RUN", "40"))
MAX_COST_USD_PER_RUN = float(os.getenv("MAX_COST_USD_PER_RUN", "0.10"))
RELEVANCE_THRESHOLD = int(os.getenv("RELEVANCE_THRESHOLD", "6"))
MAX_ITEMS_PER_COMPANY = int(os.getenv("MAX_ITEMS_PER_COMPANY", "5"))
HTTP_TIMEOUT_S = 15

# --- Storage & delivery -----------------------------------------------------
DB_PATH = os.getenv("DB_PATH", str(ROOT / "monitor.db"))
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")
DRY_RUN = os.getenv("DRY_RUN", "0") == "1"          # print digest, send nothing
MOCK_LLM = os.getenv("MOCK_LLM", "0") == "1"        # no API calls; used in tests


def load_companies(path: Path | None = None) -> list[dict]:
    with open(path or Path(__file__).parent / "companies.yaml") as f:
        return yaml.safe_load(f)["companies"]
