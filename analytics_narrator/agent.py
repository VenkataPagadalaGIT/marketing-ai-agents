"""Use case 4 (medium): Weekly Analytics Narrator.

Patterns: CODE-FIRST ANALYSIS (numbers never come from the model) + EVALUATOR-OPTIMIZER loop
          (writer drafts, critic scores against the numbers, writer fixes) + GROUNDING CHECK.

Input: a CSV export from GA4 / Search Console (date, page, clicks, impressions, sessions, conversions).
Output: a short narrative a client can read, every number traceable to the CSV.
Run:  MOCK_LLM=1 python -m analytics_narrator.agent analytics_narrator/sample_gsc.csv
"""
import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from common.llm import LLM, RunStats, config

MAX_REVISIONS = 2
PASS_SCORE = 4


class Narrative(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {
        "headline": "Clicks up 18% week over week, driven by the pricing page",
        "paragraphs": ["Organic clicks rose 18% to 1,416 this week versus 1,200 last week.",
                       "The /pricing page gained 120 clicks after its rewrite; /blog/old-post lost 90."],
        "actions": ["Refresh /blog/old-post", "Add internal links to /pricing from the top 5 blog posts"]}})
    headline: str = Field(max_length=100)
    paragraphs: list[str] = Field(min_length=1, max_length=4)
    actions: list[str] = Field(min_length=1, max_length=5)


class Critique(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {"score": 5, "issues": []}})
    score: int = Field(ge=1, le=5)
    issues: list[str]


# ---------- 1. Analysis in code -------------------------------------------------
def analyse(path: str) -> dict:
    rows = list(csv.DictReader(open(path)))
    weeks = defaultdict(lambda: defaultdict(int))        # week -> metric -> total
    pages = defaultdict(lambda: defaultdict(int))        # page -> week -> clicks
    for r in rows:
        w = r["week"]
        for m in ("clicks", "impressions", "sessions", "conversions"):
            weeks[w][m] += int(r[m])
        pages[r["page"]][w] += int(r["clicks"])
    ws = sorted(weeks)
    cur, prev = ws[-1], ws[-2]
    totals = {m: {"current": weeks[cur][m], "previous": weeks[prev][m],
                  "change_pct": round(100 * (weeks[cur][m] - weeks[prev][m]) / max(weeks[prev][m], 1), 1)}
              for m in weeks[cur]}
    movers = sorted(((p, d[cur] - d[prev], d[cur], d[prev]) for p, d in pages.items()), key=lambda x: -abs(x[1]))
    anomalies = [{"page": p, "delta": dl, "current": c, "previous": pv}
                 for p, dl, c, pv in movers[:5] if abs(dl) >= max(20, 0.15 * max(pv, 1))]
    return {"week": cur, "previous_week": prev, "totals": totals, "top_movers": anomalies}


# ---------- 2. Grounding check in code -----------------------------------------
def numbers_in(text: str) -> set[str]:
    return {n.replace(",", "").rstrip(".") for n in re.findall(r"\d[\d,]*(?:\.\d+)?", text)}


def allowed_numbers(facts: dict) -> set[str]:
    out = set()
    for m in facts["totals"].values():
        out |= {str(m["current"]), str(m["previous"]), str(abs(m["change_pct"])), str(int(abs(m["change_pct"])))}
    for a in facts["top_movers"]:
        out |= {str(abs(a["delta"])), str(a["current"]), str(a["previous"])}
    return out


def ungrounded(n: Narrative, facts: dict) -> list[str]:
    """Any number in the prose that is not in the analysis is a hallucination. Reject it."""
    ok = allowed_numbers(facts) | {"1", "2", "3", "4", "5", "7", "30", "90"}   # small counts/dates are fine
    text = n.headline + " ".join(n.paragraphs)
    return sorted(x for x in numbers_in(text) if x not in ok)


WRITER = """You write a weekly SEO performance note for a small-business client.
Use ONLY the numbers in the analysis JSON. Plain English, no hype, one headline, 1-3 short paragraphs, 1-3 actions.
Return JSON {"headline":..., "paragraphs":[...], "actions":[...]}"""

CRITIC = """You check a performance note against the analysis JSON. Score 1-5:
5 = every claim matches a number, actions follow from the data, nothing vague.
List concrete issues (quote the sentence). Return JSON {"score": int, "issues": [..]}"""


def run(path: str) -> Narrative:
    facts = analyse(path)
    llm = LLM(RunStats())
    draft, issues = None, []
    for rev in range(MAX_REVISIONS + 1):                          # evaluator-optimizer loop
        feedback = f"\n\nFix these issues: {issues}" if issues else ""
        draft = llm.structured(config.STRONG_MODEL, WRITER, f"Analysis: {facts}{feedback}", Narrative, max_tokens=500)
        bad = ungrounded(draft, facts)                            # code critic first (free)
        crit = llm.structured(config.CHEAP_MODEL, CRITIC, f"Analysis: {facts}\n\nNote: {draft.model_dump()}", Critique, max_tokens=200)
        issues = [f"number not in data: {b}" for b in bad] + crit.issues
        if not bad and crit.score >= PASS_SCORE:
            break
    status = "ok" if not issues else f"needs review: {issues}"
    out = Path(path).with_suffix(".narrative.md")
    out.write_text(f"# {draft.headline}\n\n" + "\n\n".join(draft.paragraphs) + "\n\n## Actions\n" + "\n".join(f"- {a}" for a in draft.actions))
    print(f"narrative → {out} | revisions={rev} status={status} calls={llm.stats.llm_calls} cost=${llm.stats.cost_usd:.4f}")
    return draft


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "analytics_narrator/sample_gsc.csv")
