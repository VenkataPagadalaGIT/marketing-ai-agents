import asyncio
import os
import shutil

os.environ["MOCK_LLM"] = "1"

from campaign_orchestrator import agent  # noqa: E402
from common.llm import LLM, RunStats, BudgetExceeded  # noqa: E402

BRIEF = "campaign_orchestrator/sample_brief.json"


def _clean():
    shutil.rmtree(agent.HERE / "runs" / "sample_brief", ignore_errors=True)


def test_orchestrator_runs_specialists_in_parallel_and_gates():
    _clean()
    state = asyncio.run(agent.run(BRIEF))
    assert state.status == "awaiting_approval"
    assert set(state.assets) == {"seo", "email", "social", "paid"}
    assert set(state.reviews) == set(state.assets)
    assert state.stats.llm_calls == 9                 # 1 plan + 4 specialists + 4 reviews
    assert (agent.HERE / "approvals" / "sample_brief.json").exists()


def test_checkpoints_written_for_each_stage():
    runs = agent.HERE / "runs" / "sample_brief"
    assert {p.name for p in runs.iterdir()} >= {"producing.json", "reviewing.json", "awaiting_approval.json"}


def test_resume_skips_finished_stages():
    _clean()
    asyncio.run(agent.run(BRIEF))
    (agent.HERE / "runs" / "sample_brief" / "awaiting_approval.json").unlink()      # "crash" during review
    before = (agent.HERE / "runs" / "sample_brief" / "reviewing.json").read_text()
    assert '"llm_calls": 5' in before                 # plan + 4 specialists were already paid for
    state = asyncio.run(agent.run(BRIEF, resume=True))
    assert state.status == "awaiting_approval"
    assert state.stats.llm_calls == 9                 # 5 restored + 4 reviews: no re-plan, no re-production


def test_per_agent_call_cap_is_enforced():
    llm = LLM(RunStats(), agent_call_cap=1)
    llm.structured("m", "s", "u", agent.Asset)
    import pytest
    with pytest.raises(BudgetExceeded, match="agent call cap"):
        llm.structured("m", "s", "u", agent.Asset)


def test_duplicate_tasks_get_their_own_keys():
    plan = agent.Plan(campaign_name="x", tasks=[agent.Task(agent="email", goal="a", deliverable="d"),
                                                 agent.Task(agent="email", goal="b", deliverable="d")])
    assert agent.task_keys(plan) == ["email", "email-2"]
