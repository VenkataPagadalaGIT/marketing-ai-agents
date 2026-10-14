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

from common.llm import LLM, BudgetExceeded, RunStats, config

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
    assets: dict[str, Asset] = Field(default_factory=dict)     # task key (e.g. "email" or "email-2") -> asset
    reviews: dict[str, Review] = Field(default_factory=dict)
    status: str = "planning"               # planning | producing | reviewing | awaiting_approval | failed
    stats: RunStats = Field(default_factory=RunStats)


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
promises outcomes, mentions competitors, uses medical or legal claims, or states any date, price, offer term
or URL that differs from the brief. List each problem in issues. Return JSON {"brand_score": int, "compliance_ok": bool, "issues": [..]}"""


STAGES = ("producing", "reviewing", "awaiting_approval")      # checkpoint file names, in order


def checkpoint(state: CampaignState, name: str) -> None:
    """Resume-ability: every stage writes its state; run(resume=True) restarts from the latest file."""
    d = HERE / "runs" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{state.status}.json").write_text(state.model_dump_json(indent=2))


def latest_checkpoint(name: str) -> CampaignState | None:
    d = HERE / "runs" / name
    for stage in reversed(STAGES):
        f = d / f"{stage}.json"
        if f.exists():
            return CampaignState.model_validate_json(f.read_text())
    return None


async def specialist(key: str, task: Task, state: CampaignState) -> tuple[str, Asset]:
    """Each specialist gets its own LLM object with its own call cap, on the run's shared
    counters: one runaway worker stops at PER_AGENT_CALL_CAP, the run stops at the run cap."""
    llm = LLM(state.stats, agent_call_cap=PER_AGENT_CALL_CAP)
    user = f"Brief: {json.dumps(state.brief)}\n\nTask: {task.goal}\nDeliverable: {task.deliverable}"
    asset = await asyncio.to_thread(llm.structured, config.STRONG_MODEL, SPECIALIST[task.agent] + SPECIALIST_SUFFIX, user, Asset, 1200)
    return key, asset


def task_keys(plan: Plan) -> list[str]:
    """One key per task, so two email tasks become "email" and "email-2" instead of overwriting."""
    keys, seen = [], {}
    for t in plan.tasks:
        seen[t.agent] = seen.get(t.agent, 0) + 1
        keys.append(t.agent if seen[t.agent] == 1 else f"{t.agent}-{seen[t.agent]}")
    return keys


async def run(brief_path: str, resume: bool = False) -> CampaignState:
    run_name = Path(brief_path).stem
    state = latest_checkpoint(run_name) if resume else None
    if state is not None:
        print(f"resuming {run_name} from checkpoint '{state.status}'")
    else:
        state = CampaignState(brief=json.loads(Path(brief_path).read_text()))
    llm = LLM(state.stats)

    # 1. plan (one strong-model call); skipped when resuming past it
    if state.plan is None:
        state.plan = llm.structured(config.STRONG_MODEL, MANAGER, json.dumps(state.brief), Plan, max_tokens=700)
        state.status = "producing"; checkpoint(state, run_name)

    # 2. specialists in parallel; the slowest one sets the wall-clock, not the sum
    if state.status == "producing":
        keys = task_keys(state.plan)
        todo = [(k, t) for k, t in zip(keys, state.plan.tasks) if k not in state.assets]
        results = await asyncio.gather(*(specialist(k, t, state) for k, t in todo), return_exceptions=True)
        for (k, _), r in zip(todo, results):
            if isinstance(r, Exception):          # one failure does not sink the rest; it is reported
                state.stats.errors.append(f"{k}: {r!r}"); continue
            state.assets[r[0]] = r[1]
        state.status = "reviewing" if state.assets else "failed"
        checkpoint(state, run_name)
        if state.status == "failed":
            print(f"no asset was produced; errors: {state.stats.errors}")
            return state

    # 3. independent reviewer (cheap model) scores every asset; the producer never grades itself
    if state.status == "reviewing":
        for key, asset in state.assets.items():
            if key in state.reviews:
                continue
            user = f"Brief: {json.dumps(state.brief)}\n\nAsset from {key}: {asset.model_dump_json()}"
            try:
                state.reviews[key] = llm.structured(config.CHEAP_MODEL, REVIEWER, user, Review, max_tokens=250)
            except BudgetExceeded as e:
                state.stats.errors.append(f"review stopped: {e}"); break
        state.status = "awaiting_approval"; checkpoint(state, run_name)

    # 4. gate: nothing leaves without a person. Flagged and unreviewed assets go on top.
    flagged = [k for k in state.assets if k not in state.reviews
               or not state.reviews[k].compliance_ok or state.reviews[k].brand_score < 4]
    missing = [k for k in task_keys(state.plan) if k not in state.assets]
    bundle = HERE / "approvals" / f"{run_name}.json"
    bundle.parent.mkdir(exist_ok=True)
    bundle.write_text(json.dumps({"flagged": flagged, "missing": missing, "errors": state.stats.errors,
                                  "assets": {k: v.model_dump() for k, v in state.assets.items()},
                                  "reviews": {k: v.model_dump() for k, v in state.reviews.items()}}, indent=2))
    print(f"{state.plan.campaign_name}: {len(state.assets)} assets, flagged={flagged} → {bundle}")
    print(f"calls={state.stats.llm_calls} cost=${state.stats.cost_usd:.4f} errors={state.stats.errors}")
    return state


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    asyncio.run(run(args[0] if args else "campaign_orchestrator/sample_brief.json", resume="--resume" in sys.argv))
