"""Output contracts. Every LLM response is validated against these before use."""
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field


class Item(BaseModel):
    """One thing a company published. Produced by collect.py, never by the LLM."""
    company: str
    source: str                      # blog | news | pricing | youtube | social
    url: str
    title: str
    text: str = Field(max_length=6000)   # trimmed article text; keeps tokens low
    published_at: datetime | None = None
    page_text: str | None = Field(default=None, exclude=True)   # full page text, pages only
    content_hash: str                # feeds: sha256(url, title); pages: sha256(url, full text)


class Judgement(BaseModel):
    """LLM step 1: is this worth Priya's attention?"""
    model_config = ConfigDict(json_schema_extra={"example": {"relevance": 8, "category": "product", "reason": "mock: new feature announced"}})
    relevance: int = Field(ge=0, le=10)
    category: str = Field(pattern="^(product|pricing|funding|hiring|content|partnership|other)$")
    reason: str = Field(max_length=200)


class Summary(BaseModel):
    """LLM step 2: the digest line."""
    model_config = ConfigDict(json_schema_extra={"example": {
        "headline": "Mock headline for a competitor update",
        "summary": "This is a mocked summary used only in local tests and dry runs.",
        "why_it_matters": "Mock: shows the pipeline works end to end without spending money."}})
    headline: str = Field(max_length=90)
    summary: str = Field(min_length=20, max_length=400)
    why_it_matters: str = Field(min_length=10, max_length=250)


class DigestEntry(BaseModel):
    company: str
    source: str
    url: str
    content_hash: str
    judgement: Judgement
    summary: Summary


class RunStats(BaseModel):
    items_fetched: int = 0
    items_new: int = 0
    items_sent: int = 0
    llm_calls: int = 0
    cost_usd: float = 0.0
    errors: list[str] = Field(default_factory=list)
