"""Use case 3 (medium): SEO Content Brief Agent.

Patterns: ROUTING (intent → template) + TOOL-USING AGENT LOOP (ReAct) + RETRIEVAL (brand facts)
          + STRUCTURED OUTPUT with code validation.

The model decides which tools to call and when it has enough; code enforces the limits.
Run:  MOCK_LLM=1 python -m content_brief.agent "best crm for small agencies"
"""
import json
import sqlite3
import sys
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from common.llm import LLM, RunStats, config

MAX_STEPS = 8          # the loop ends here no matter what the model wants
HERE = Path(__file__).parent


# ---------- Schemas (contracts between steps) --------------------------------
class Route(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {"intent": "commercial", "template": "comparison"}})
    intent: str = Field(pattern="^(informational|commercial|transactional|navigational)$")
    template: str = Field(pattern="^(howto|comparison|listicle|landing|glossary)$")


class Action(BaseModel):
    """What the model wants next: a tool call or 'finish'."""
    model_config = ConfigDict(json_schema_extra={"example": {"thought": "mock", "tool": "finish", "args": {}}})
    thought: str = Field(max_length=200)
    tool: str = Field(pattern="^(search_serp|fetch_page|keyword_data|brand_facts|finish)$")
    args: dict = {}


class Brief(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {
        "title": "Best CRM for Small Agencies in 2026: 7 Options Compared",
        "primary_keyword": "best crm for small agencies",
        "secondary_keywords": ["agency crm", "crm for marketing agencies", "small business crm"],
        "search_intent": "commercial", "target_word_count": 1800,
        "outline": ["What agencies need from a CRM", "How we compared", "The 7 options", "Pricing table", "Our pick by agency size", "FAQ"],
        "faqs": ["Do I need a CRM with 5 clients?", "Can a CRM replace my project tool?", "What does a CRM cost per seat?"],
        "internal_links": ["/blog/agency-reporting", "/services/crm-setup"],
        "brand_facts_used": ["Sprout serves 300+ local clients"], "sources": ["https://example.com/serp-1"]}})
    title: str = Field(max_length=70)
    primary_keyword: str
    secondary_keywords: list[str] = Field(min_length=3, max_length=12)
    search_intent: str
    target_word_count: int = Field(ge=600, le=4000)
    outline: list[str] = Field(min_length=4, max_length=14)
    faqs: list[str] = Field(min_length=3, max_length=8)
    internal_links: list[str]
    brand_facts_used: list[str]
    sources: list[str] = Field(min_length=1)


# ---------- Tools (each: one verb, trimmed output, safe by default) -----------
def search_serp(q: str, **_) -> list[dict]:
    """Top-10 results. Replace the stub with a DataForSEO / Semrush / SerpAPI call."""
    fixture = HERE / "fixtures" / "serp.json"
    if fixture.exists():
        return json.loads(fixture.read_text())[:10]
    return []


def fetch_page(url: str, **_) -> dict:
    """Returns {url, h2s, word_count} only, never the full HTML (token discipline)."""
    fixture = HERE / "fixtures" / "pages.json"
    pages = json.loads(fixture.read_text()) if fixture.exists() else {}
    return pages.get(url, {"url": url, "h2s": [], "word_count": 0})


def keyword_data(keyword: str, **_) -> dict:
    """Volume/difficulty. Stub; wire to your keyword API."""
    return {"keyword": keyword, "volume": 2400, "difficulty": 38, "related": ["agency crm", "crm for marketing agencies", "small business crm"]}


def brand_facts(query: str, **_) -> list[str]:
    """RETRIEVAL. Full-text search over brand docs in SQLite FTS5; no vector DB needed at this size."""
    db = HERE / "brand.db"
    if not db.exists():
        return ["Sprout serves 300+ local clients", "Sprout offers CRM setup as a service at /services/crm-setup"]
    con = sqlite3.connect(db)
    rows = con.execute("SELECT fact FROM facts WHERE facts MATCH ? LIMIT 5", (query,)).fetchall()
    return [r[0] for r in rows]


TOOLS = {"search_serp": search_serp, "fetch_page": fetch_page, "keyword_data": keyword_data, "brand_facts": brand_facts}

ROUTER_SYSTEM = """Classify the keyword's search intent and pick a content template.
Return JSON {"intent":"informational|commercial|transactional|navigational","template":"howto|comparison|listicle|landing|glossary"}"""

AGENT_SYSTEM = """You are an SEO strategist gathering research for a content brief.
Tools (call ONE per step): search_serp(q), fetch_page(url), keyword_data(keyword), brand_facts(query), finish().
Call search_serp first. Fetch at most 3 top pages. Call brand_facts once. Call finish when you have enough.
Return JSON {"thought":"<=30 words","tool":"...","args":{...}}"""

BRIEF_SYSTEM = """Write an SEO content brief as JSON matching the Brief schema. Use ONLY the research notes given.
Outline must cover what the top pages cover plus one angle they miss. Include every brand fact that is relevant."""


def run(keyword: str) -> Brief:
    llm = LLM(RunStats())
    route = llm.structured(config.CHEAP_MODEL, ROUTER_SYSTEM, keyword, Route, max_tokens=60)

    notes: list[str] = [f"keyword={keyword} intent={route.intent} template={route.template}"]
    fetched = 0
    for step in range(MAX_STEPS):                                   # the agent loop
        action = llm.structured(config.CHEAP_MODEL, AGENT_SYSTEM,
                                "Research so far:\n" + "\n".join(notes[-12:]), Action, max_tokens=150)
        if action.tool == "finish":
            break
        if action.tool == "fetch_page":
            if fetched >= 3:
                notes.append("fetch_page refused: limit 3")
                continue
            fetched += 1
        result = TOOLS[action.tool](**action.args)                  # code runs the tool, not the model
        notes.append(f"{action.tool}({action.args}) -> {json.dumps(result)[:1200]}")

    brief = llm.structured(config.STRONG_MODEL, BRIEF_SYSTEM, "\n".join(notes), Brief, max_tokens=900)
    # code checks the model cannot skip
    assert keyword.lower() in brief.primary_keyword.lower(), "primary keyword drifted"
    out = HERE / "briefs" / f"{keyword.replace(' ', '-')}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(brief.model_dump_json(indent=2))
    print(f"brief → {out} | steps={step + 1} calls={llm.stats.llm_calls} cost=${llm.stats.cost_usd:.4f}")
    return brief


if __name__ == "__main__":
    run(" ".join(sys.argv[1:]) or "best crm for small agencies")
