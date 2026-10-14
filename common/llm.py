"""One thin door to every model.

OpenRouter speaks the OpenAI API, so the official OpenAI SDK works unchanged: swap the
base_url and the model id string, nothing else.

Every call:
  * asks for JSON and validates it against a Pydantic schema (one retry with the error),
  * tells the model the exact schema it must return,
  * records the cost OpenRouter reports in `usage.cost`,
  * checks the caps BEFORE the call (run-wide calls and dollars, plus an optional
    per-agent call cap), so a run stops cleanly instead of spending past its limit.

With MOCK_LLM=1 nothing touches the network: each schema returns the example stored in
its own `model_config["json_schema_extra"]["example"]`.
"""
import json
import logging
import os
import re
import threading
from typing import TypeVar

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, Field, ValidationError

load_dotenv()

REPO_URL = "https://github.com/VenkataPagadalaGIT/marketing-ai-agents"


class RunStats(BaseModel):
    """Shared per-run counters. Every agent in this repo reports the same numbers."""
    llm_calls: int = 0
    cost_usd: float = 0.0
    errors: list[str] = Field(default_factory=list)


class Config:
    """Read once from the environment. Same knobs for every agent."""
    OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
    OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
    CHEAP_MODEL = os.getenv("CHEAP_MODEL", "anthropic/claude-haiku-5.5")
    STRONG_MODEL = os.getenv("STRONG_MODEL", "anthropic/claude-sonnet-5.5")
    FALLBACK_MODELS = [m for m in os.getenv("FALLBACK_MODELS", "openai/gpt-5-mini,google/gemini-3.5-flash").split(",") if m]
    MAX_LLM_CALLS_PER_RUN = int(os.getenv("MAX_LLM_CALLS_PER_RUN", "60"))
    MAX_COST_USD_PER_RUN = float(os.getenv("MAX_COST_USD_PER_RUN", "0.50"))
    MOCK_LLM = os.getenv("MOCK_LLM", "0") == "1"
    # Reasoning models (Sonnet 5.5, GPT-5 mini) think before answering, and the thinking counts
    # against max_tokens. Low effort plus headroom keeps the JSON from being cut off.
    REASONING_EFFORT = os.getenv("REASONING_EFFORT", "low")
    TOKEN_HEADROOM = int(os.getenv("TOKEN_HEADROOM", "1024"))
    AGENT_ENABLED = os.getenv("AGENT_ENABLED", "1") == "1"      # kill switch: 0 stops every model call


config = Config

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)
_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


class BudgetExceeded(RuntimeError):
    """Raised when a run hits a call or dollar cap. The run stops; it does not retry."""


class AgentDisabled(RuntimeError):
    """Raised when AGENT_ENABLED=0. The kill switch beats every other setting."""


class LLM:
    """
    stats      shared RunStats for the whole run (several LLM objects can share one).
    max_calls  run-wide call cap, counted on `stats` (default MAX_LLM_CALLS_PER_RUN).
    max_cost   run-wide dollar cap, counted on `stats` (default MAX_COST_USD_PER_RUN).
    agent_call_cap  optional cap on calls made through THIS object only, e.g. one
               specialist in an orchestrator, so one runaway worker cannot spend the
               whole run's budget.
    """

    _lock = threading.Lock()   # agents call from worker threads (asyncio.to_thread)

    def __init__(self, stats: RunStats, mock: dict | None = None, *, max_calls: int | None = None,
                 max_cost: float | None = None, agent_call_cap: int | None = None):
        self.stats = stats
        self.mock = mock if mock is not None else ({"_default": True} if config.MOCK_LLM else {})
        self.max_calls = max_calls if max_calls is not None else config.MAX_LLM_CALLS_PER_RUN
        self.max_cost = max_cost if max_cost is not None else config.MAX_COST_USD_PER_RUN
        self.agent_call_cap = agent_call_cap
        self.calls = 0                       # calls through this object
        self.client = None if self.mock else OpenAI(
            base_url=config.OPENROUTER_BASE_URL,
            api_key=config.OPENROUTER_API_KEY,
            default_headers={  # OpenRouter shows these per app in its dashboard
                "HTTP-Referer": REPO_URL,
                "X-Title": "marketing-ai-agents",
            },
        )

    # ---- limits -----------------------------------------------------------------
    def _reserve(self) -> None:
        """Check every cap and count the call, atomically, before it is made."""
        if not config.AGENT_ENABLED:
            raise AgentDisabled("AGENT_ENABLED=0: kill switch is on")
        with self._lock:
            if self.stats.llm_calls >= self.max_calls:
                raise BudgetExceeded(f"call cap {self.max_calls} reached")
            if self.stats.cost_usd >= self.max_cost:
                raise BudgetExceeded(f"cost cap ${self.max_cost:.2f} reached")
            if self.agent_call_cap is not None and self.calls >= self.agent_call_cap:
                raise BudgetExceeded(f"agent call cap {self.agent_call_cap} reached")
            self.stats.llm_calls += 1
            self.calls += 1

    def _add_cost(self, cost: float | None) -> None:
        if cost is not None:
            with self._lock:
                self.stats.cost_usd += float(cost)

    # ---- the one public method --------------------------------------------------
    def structured(self, model: str, system: str, user: str, schema: type[T], max_tokens: int = 400) -> T:
        """Ask for JSON matching `schema`; retry once with the validation error as feedback.
        Each attempt is checked against the caps first, retries included."""
        if self.mock:
            self._reserve()
            return self._mock_response(schema, user)

        system = (f"{system}\n\nReturn only a JSON object that validates against this JSON Schema:\n"
                  f"{json.dumps(schema.model_json_schema())}")
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        last_err: Exception | None = None
        for attempt in range(2):
            self._reserve()
            resp = self.client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=max_tokens + config.TOKEN_HEADROOM,      # answer budget plus room for reasoning
                response_format={"type": "json_object"},
                extra_body={
                    "models": [model, *[m for m in config.FALLBACK_MODELS if m != model]],  # fallback chain
                    "provider": {"sort": "price"},     # cheapest provider serving this model
                    "reasoning": {"effort": config.REASONING_EFFORT},
                },
            )
            usage = getattr(resp, "usage", None)
            self._add_cost(getattr(usage, "cost", None) if usage else None)   # OpenRouter returns cost by default
            raw = (resp.choices[0].message.content or "{}") if resp.choices else "{}"
            try:
                return schema.model_validate(json.loads(_FENCE.sub("", raw)))
            except (ValidationError, json.JSONDecodeError) as e:
                last_err = e
                log.warning("schema fail attempt %d for %s: %s", attempt, schema.__name__, e)
                messages.append({"role": "assistant", "content": raw})
                messages.append({"role": "user", "content": f"Invalid. Fix it and return only JSON matching the schema. Error: {e}"})
        raise ValueError(f"model never produced a valid {schema.__name__}: {last_err}")

    def _mock_response(self, schema: type[T], user: str = "") -> T:
        """Deterministic fake for tests and dry runs. An agent may register a function per
        schema name in `mock` (input text -> dict) so demos look realistic; otherwise each
        schema returns its own `model_config["json_schema_extra"]["example"]`."""
        fake = self.mock.get(schema.__name__)
        if callable(fake):
            return schema.model_validate(fake(user))
        example = (schema.model_config.get("json_schema_extra") or {}).get("example")
        if example is None:
            raise ValueError(f"{schema.__name__} needs a json_schema_extra example for MOCK_LLM")
        return schema.model_validate(example)
