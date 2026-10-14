"""Use case 6 (complex): Multi-client SEO Audit Swarm.

Patterns: FAN-OUT / FAN-IN over pages (parallelization) + ROUTING HANDOFFS (triage agent hands each
          issue to a technical / content / links specialist) + MULTI-TENANT budgets and config
          + QUEUE + WORKERS (bounded concurrency) + IDEMPOTENT jobs + AGGREGATION in code
          + EVALUATOR over the final report.

A crawl export (one row per page) → per-page triage (cheap, parallel, capped per client)
→ issues handed to specialists (strong model, only for the pages that need it)
→ code aggregates into a client report → critic checks the report → file per client.
Run:  MOCK_LLM=1 python -m seo_audit_swarm.agent seo_audit_swarm/sample_crawl.csv
"""
import asyncio
import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from common.llm import LLM, RunStats, config

HERE = Path(__file__).parent
CONCURRENCY = 8                           # parallel LLM calls per worker process
TENANTS = {                               # per-client config; in prod this is a table
    "acme-dental":  {"max_pages": 200, "max_cost_usd": 2.00, "priority": 1},
    "bob-plumbing": {"max_pages": 50,  "max_cost_usd": 0.50, "priority": 2},
}


class Triage(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {"issues": ["thin_content", "missing_h1"], "handoff": "content", "severity": 3}})
    issues: list[str]
    handoff: str = Field(pattern="^(technical|content|links|none)$")
    severity: int = Field(ge=0, le=5)


class Fix(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {"recommendation": "Expand the page to 800+ words covering service details, pricing ranges and an FAQ; add one H1 matching the title.", "effort": "medium", "impact": 4}})
    recommendation: str = Field(min_length=30, max_length=600)
    effort: str = Field(pattern="^(low|medium|high)$")
    impact: int = Field(ge=1, le=5)


class ReportCheck(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {"ok": True, "issues": []}})
    ok: bool
    issues: list[str]


TRIAGE = """You triage one crawled page for SEO problems using ONLY the fields given (status, title_len,
meta_len, h1_count, word_count, internal_links_in, canonical_ok). List issue codes from:
missing_title, long_title, missing_meta, missing_h1, multi_h1, thin_content, orphan_page, bad_canonical, error_status.
handoff: technical (status/canonical), content (title/meta/h1/thin), links (orphan), none.
Return JSON {"issues":[..],"handoff":"...","severity":0-5}"""

SPECIALIST = {
    "technical": "You are a technical SEO. Give one concrete fix for the page's issues.",
    "content":   "You are a content SEO. Give one concrete fix for the page's issues.",
    "links":     "You are an internal-linking specialist. Give one concrete fix for the page's issues.",
}
SUFFIX = ' Return JSON {"recommendation": str, "effort": "low|medium|high", "impact": 1-5}'
CRITIC = """Check this SEO audit report for a client: counts must match the per-page data, every top recommendation
must reference a page in the data, no vague advice. Return JSON {"ok": bool, "issues": [..]}"""


class Worker:
    """One worker = one client's job. Bounded concurrency, idempotent per page, budget-capped per tenant."""

    def __init__(self, tenant: str, pages: list[dict]):
        self.tenant, self.cfg = tenant, TENANTS[tenant]
        self.pages = pages[: self.cfg["max_pages"]]
        self.stats = RunStats()
        self.llm = LLM(self.stats)
        self.sem = asyncio.Semaphore(CONCURRENCY)
        self.cache = HERE / "cache" / tenant
        self.cache.mkdir(parents=True, exist_ok=True)

    def _budget_ok(self) -> bool:
        return self.stats.cost_usd < self.cfg["max_cost_usd"]

    async def triage_page(self, page: dict) -> dict:
        key = self.cache / (hashlib.sha1(json.dumps(page, sort_keys=True).encode()).hexdigest() + ".json")
        if key.exists():                                    # idempotent: a rerun costs nothing
            return json.loads(key.read_text())
        if not self._budget_ok():
            return {"page": page["url"], "skipped": "budget"}
        async with self.sem:
            t = await asyncio.to_thread(self.llm.structured, config.CHEAP_MODEL, TRIAGE, json.dumps(page), Triage, 120)
        result = {"page": page["url"], **t.model_dump()}
        if t.handoff != "none" and t.severity >= 3 and self._budget_ok():      # handoff to a specialist
            async with self.sem:
                fix = await asyncio.to_thread(self.llm.structured, config.STRONG_MODEL,
                                              SPECIALIST[t.handoff] + SUFFIX,
                                              f"Page: {json.dumps(page)}\nIssues: {t.issues}", Fix, 300)
            result["fix"] = fix.model_dump()
        key.write_text(json.dumps(result))
        return result

    async def run(self) -> dict:
        results = await asyncio.gather(*(self.triage_page(p) for p in self.pages))
        # aggregation is code: counts can't hallucinate
        issue_counts = Counter(i for r in results for i in r.get("issues", []))
        fixes = sorted((r for r in results if "fix" in r), key=lambda r: (-r["fix"]["impact"], r["fix"]["effort"]))
        report = {
            "client": self.tenant, "pages_audited": len(results),
            "pages_skipped_for_budget": sum(1 for r in results if r.get("skipped")),
            "issue_counts": dict(issue_counts), "top_fixes": fixes[:10],
        }
        check = self.llm.structured(config.CHEAP_MODEL, CRITIC, json.dumps(report)[:6000], ReportCheck, max_tokens=200) \
            if self._budget_ok() else ReportCheck(ok=False, issues=["budget exhausted before review"])
        report["review"] = check.model_dump()
        report["llm_calls"], report["cost_usd"] = self.stats.llm_calls, round(self.stats.cost_usd, 4)
        out = HERE / "reports" / f"{self.tenant}.json"
        out.parent.mkdir(exist_ok=True)
        out.write_text(json.dumps(report, indent=2))
        print(f"{self.tenant}: {report['pages_audited']} pages, {len(fixes)} fixes, calls={report['llm_calls']} cost=${report['cost_usd']} review_ok={check.ok}")
        return report


async def run(crawl_csv: str) -> list[dict]:
    by_tenant: dict[str, list[dict]] = defaultdict(list)
    for row in csv.DictReader(open(crawl_csv)):
        by_tenant[row.pop("client")].append(row)
    # queue: higher-priority tenants first; each tenant is one worker job
    jobs = sorted(by_tenant.items(), key=lambda kv: TENANTS[kv[0]]["priority"])
    return [await Worker(t, pages).run() for t, pages in jobs]


if __name__ == "__main__":
    asyncio.run(run(sys.argv[1] if len(sys.argv) > 1 else "seo_audit_swarm/sample_crawl.csv"))
