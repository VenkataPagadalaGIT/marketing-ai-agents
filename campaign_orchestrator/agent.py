"""Use case 5 (complex): Campaign Launch Orchestrator.

Patterns: ORCHESTRATOR-WORKERS (manager plans, specialists execute) + PARALLELIZATION (asyncio)
          + SHARED STATE (typed CampaignState, not chat transcripts) + PER-AGENT BUDGETS
          + HUMAN APPROVAL GATE before anything leaves the system + CHECKPOINTS for resume.

Brief in → manager writes a plan → SEO / email / social / paid specialists run in parallel
→ brand reviewer scores every asset → bundle waits in /approvals for a human.
Run:  MOCK_LLM=1 python -m campaign_orchestrator.agent campaign_orchestrator/sample_brief.json
"""
import asyncio
import json
import sys
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from common.llm import LLM, RunStats, config

HERE = Path(__file__).parent
SPECIALISTS = ("seo", "email", "social", "paid")
PER_AGENT_CALL_CAP = 6


# ---------- Shared state: the only thing agents pass to each other -------------
class Task(BaseModel):
    agent: str = Field(pattern="^(seo|email|social|paid)$")
    goal: str = Field(max_length=300)
    deliverable: str = Field(max_length=120)


class Plan(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {"campaign_name": "Fall Smile Promo", "tasks": [
        {"agent": "seo", "goal": "Draft a landing page outline targeting 'teeth whitening Atlanta'", "deliverable": "landing page outline"},
        {"agent": "email", "goal": "Write a 3-email sequence to lapsed patients", "deliverable": "email sequence"},
        {"agent": "social", "goal": "Write 5 Instagram captions announcing the promo", "deliverable": "captions"},
        {"agent": "paid", "goal": "Write 3 Google Ads RSA headline/description sets", "deliverable": "ad copy"}]}})
    campaign_name: str
    tasks: list[Task] = Field(min_length=2, max_length=6)


class Asset(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {"title": "Mock asset", "body": "Mock body copy for a campaign asset generated in a local test run without any API call.", "notes": "mock"}})
    title: str = Field(max_length=120)
    body: str = Field(min_length=40, max_length=4000)
    notes: str = Field(max_length=300)


class Review(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {"brand_score": 5, "compliance_ok": True, "issues": []}})
    brand_score: int = Field(ge=1, le=5)
    compliance_ok: bool
    issues: list[str]


class CampaignState(BaseModel):
    brief: dict
    plan: Plan | None = None
    assets: dict[str, Asset] = {}          # agent name -> asset
    reviews: dict[str, Review] = {}
    status: str = "planning"               # planning | producing | reviewing | awaiting_approval | failed
    stats: RunStats = RunStats()


MANAGER = """You are a campaign manager. Turn the brief into 2-6 tasks for specialists: seo, email, social, paid.
Each task: one concrete deliverable. Do not write the assets yourself.
Return JSON {"campaign_name": str, "tasks": [{"agent":..., "goal":..., "deliverable":...}]}"""

SPECIALIST = {
    "seo":    "You are an SEO specialist. Produce the deliverable as publish-ready copy with H1/H2 structure.",
    "email":  "You are an email marketer. Produce the deliverable; each email has a subject line and under 150 words.",
    "social": "You are a social media writer. Produce the deliverable; captions under 220 characters, no hashtags spam.",
    "paid":   "You are a PPC copywriter. Produce the deliverable; RSA headlines ≤30 chars, descriptions ≤90 chars.",
}
SPECIALIST_SUFFIX = ' Use only facts from the brief. Return JSON {"title":..., "body":..., "notes":"assumptions or gaps"}'

REVIEWER = """You are the brand and compliance reviewer. Score brand fit 1-5. compliance_ok=false if the copy
promises outcomes, mentions competitors, uses medical/legal claims, or includes pricing not in the brief.
Return JSON {"brand_score": int, "compliance_ok": bool, "issues": [..]}"""


def checkpoint(state: CampaignState, name: str) -> None:
    """Resume-ability: every stage writes its state. A crash restarts from the last file."""
    d = HERE / "runs" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{state.status}.json").write_text(state.model_dump_json(indent=2))


async def specialist(name: str, task: Task, state: CampaignState) -> tuple[str, Asset]:
    """Each specialist gets its own LLM wrapper with its own small cap, sharing the run's counters."""
    llm = LLM(state.stats)
    calls_before = state.stats.llm_calls
    user = f"Brief: {json.dumps(state.brief)}\n\nTask: {task.goal}\nDeliverable: {task.deliverable}"
    asset = await asyncio.to_thread(llm.structured, config.STRONG_MODEL, SPECIALIST[name] + SPECIALIST_SUFFIX, user, Asset, 1200)
    assert state.stats.llm_calls - calls_before <= PER_AGENT_CALL_CAP
    return name, asset


async def run(brief_path: str) -> CampaignState:
    state = CampaignState(brief=json.loads(Path(brief_path).read_text()))
    run_name = Path(brief_path).stem
    llm = LLM(state.stats)

    # 1. plan (one strong-model call)
    state.plan = llm.structured(config.STRONG_MODEL, MANAGER, json.dumps(state.brief), Plan, max_tokens=700)
    state.status = "producing"; checkpoint(state, run_name)

    # 2. specialists in parallel; the slowest one sets the wall-clock, not the sum
    results = await asyncio.gather(*(specialist(t.agent, t, state) for t in state.plan.tasks), return_exceptions=True)
    for r in results:
        if isinstance(r, Exception):
            state.stats.errors.append(str(r)); continue
        name, asset = r
        state.assets[name] = asset
    state.status = "reviewing"; checkpoint(state, run_name)

    # 3. independent reviewer (cheap model) scores every asset
    for name, asset in state.assets.items():
        user = f"Brief: {json.dumps(state.brief)}\n\nAsset from {name}: {asset.model_dump()}"
        state.reviews[name] = llm.structured(config.CHEAP_MODEL, REVIEWER, user, Review, max_tokens=250)

    # 4. gate: nothing leaves without a person. Failed reviews are flagged, not auto-fixed forever.
    flagged = [n for n, r in state.reviews.items() if not r.compliance_ok or r.brand_score < 4]
    state.status = "awaiting_approval"; checkpoint(state, run_name)
    bundle = HERE / "approvals" / f"{run_name}.json"
    bundle.parent.mkdir(exist_ok=True)
    bundle.write_text(json.dumps({"flagged": flagged, "assets": {k: v.model_dump() for k, v in state.assets.items()},
                                  "reviews": {k: v.model_dump() for k, v in state.reviews.items()}}, indent=2))
    print(f"{state.plan.campaign_name}: {len(state.assets)} assets, flagged={flagged} → {bundle}")
    print(f"calls={state.stats.llm_calls} cost=${state.stats.cost_usd:.4f} errors={state.stats.errors}")
    return state


if __name__ == "__main__":
    asyncio.run(run(sys.argv[1] if len(sys.argv) > 1 else "campaign_orchestrator/sample_brief.json"))
