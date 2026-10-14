"""One thin door to every model. OpenRouter speaks the OpenAI API, so the official
OpenAI SDK works unchanged: swap the base_url and the model id string, nothing else.

Every call: structured JSON validated by a Pydantic schema, cost tracked, hard caps enforced.
"""
import json
import logging
from typing import TypeVar

from openai import OpenAI
from pydantic import BaseModel, ValidationError

from . import config
from .schemas import RunStats

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


class BudgetExceeded(RuntimeError):
    """Raised when a run hits its call or dollar cap. The run stops; it does not retry."""


class LLM:
    def __init__(self, stats: RunStats, mock: dict | None = None):
        self.stats = stats
        self.mock = mock or ({} if not config.MOCK_LLM else {"_default": True})
        self.client = None if self.mock else OpenAI(
            base_url=config.OPENROUTER_BASE_URL,
            api_key=config.OPENROUTER_API_KEY,
            default_headers={  # OpenRouter shows these in its dashboard per app
                "HTTP-Referer": "https://github.com/your-org/competitor-monitor",
                "X-Title": "competitor-monitor",
            },
        )

    def _guard(self):
        if self.stats.llm_calls >= config.MAX_LLM_CALLS_PER_RUN:
            raise BudgetExceeded(f"call cap {config.MAX_LLM_CALLS_PER_RUN} reached")
        if self.stats.cost_usd >= config.MAX_COST_USD_PER_RUN:
            raise BudgetExceeded(f"cost cap ${config.MAX_COST_USD_PER_RUN} reached")

    def structured(self, model: str, system: str, user: str, schema: type[T], max_tokens: int = 400) -> T:
        """Ask for JSON matching `schema`; retry once with the validation error as feedback."""
        self._guard()
        if self.mock:
            return self._mock_response(schema)

        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        last_err = None
        for attempt in range(2):
            resp = self.client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=0.1,
                response_format={"type": "json_object"},
                extra_body={
                    "models": [model, *config.FALLBACK_MODELS],  # OpenRouter fallback chain
                    "usage": {"include": True},                  # returns cost in the response
                    "provider": {"sort": "price"},               # cheapest provider for this model
                },
            )
            self.stats.llm_calls += 1
            usage = getattr(resp, "usage", None)
            cost = getattr(usage, "cost", None) if usage else None
            if cost is not None:
                self.stats.cost_usd += float(cost)
            raw = resp.choices[0].message.content or "{}"
            try:
                return schema.model_validate(json.loads(raw))
            except (ValidationError, json.JSONDecodeError) as e:
                last_err = e
                log.warning("schema fail attempt %d: %s", attempt, e)
                messages.append({"role": "assistant", "content": raw})
                messages.append({"role": "user", "content": f"Invalid. Fix and return only JSON matching the schema. Error: {e}"})
        raise ValueError(f"model never produced valid {schema.__name__}: {last_err}")

    def _mock_response(self, schema: type[T]) -> T:
        """Deterministic fake used by tests and DRY runs. Never hits the network."""
        self.stats.llm_calls += 1
        if schema.__name__ == "Judgement":
            return schema.model_validate({"relevance": 8, "category": "product", "reason": "mock: new feature announced"})
        if schema.__name__ == "Summary":
            return schema.model_validate({
                "headline": "Mock headline for a competitor update",
                "summary": "This is a mocked summary used only in local tests and dry runs.",
                "why_it_matters": "Mock: shows the pipeline works end to end without spending money.",
            })
        return schema()
