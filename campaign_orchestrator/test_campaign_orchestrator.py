import asyncio
import os
os.environ["MOCK_LLM"] = "1"

from campaign_orchestrator import agent  # noqa: E402


def test_orchestrator_runs_specialists_in_parallel_and_gates():
    state = asyncio.run(agent.run("campaign_orchestrator/sample_brief.json"))
    assert state.status == "awaiting_approval"
    assert set(state.assets) == {"seo", "email", "social", "paid"}
    assert set(state.reviews) == set(state.assets)
    # 1 plan + 4 specialists + 4 reviews
    assert state.stats.llm_calls == 9
    assert (agent.HERE / "approvals" / "sample_brief.json").exists()


def test_checkpoints_written_for_each_stage():
    runs = agent.HERE / "runs" / "sample_brief"
    assert {p.name for p in runs.iterdir()} >= {"producing.json", "reviewing.json", "awaiting_approval.json"}
