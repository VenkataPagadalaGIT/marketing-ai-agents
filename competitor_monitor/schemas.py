"""Output contracts. Every LLM response is validated against these before use."""
from datetime import datetime
from pydantic import BaseModel, Field


class Item(BaseModel):
    """One thing a company published. Produced by collect.py, never by the LLM."""
    company: str
    source: str                      # blog | news | pricing | youtube | social
    url: str
    title: str
    text: str = Field(max_length=6000)   # trimmed article text; keeps tokens low
    published_at: datetime | None = None
    content_hash: str                # sha256 of url+title+first 500 chars


class Judgement(BaseModel):
    """LLM step 1: is this worth Priya's attention?"""
    relevance: int = Field(ge=0, le=10)
    category: str = Field(pattern="^(product|pricing|funding|hiring|content|partnership|other)$")
    reason: str = Field(max_length=200)


class Summary(BaseModel):
    """LLM step 2: the digest line."""
    headline: str = Field(max_length=90)
    summary: str = Field(min_length=20, max_length=400)
    why_it_matters: str = Field(min_length=10, max_length=250)


class DigestEntry(BaseModel):
    company: str
    source: str
    url: str
    judgement: Judgement
    summary: Summary


class RunStats(BaseModel):
    items_fetched: int = 0
    items_new: int = 0
    items_sent: int = 0
    llm_calls: int = 0
    cost_usd: float = 0.0
    errors: list[str] = []
