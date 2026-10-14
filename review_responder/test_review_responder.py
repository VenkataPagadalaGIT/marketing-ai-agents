import os
os.environ["MOCK_LLM"] = "1"

from common.llm import LLM, RunStats  # noqa: E402
from review_responder import agent  # noqa: E402


def test_guardrails_catch_promises_and_missing_phone():
    cls = agent.Classification(sentiment="negative", issue="billing", needs_escalation=False, mentions_staff=False)
    bad = "We guarantee a full refund and a free visit."
    probs = agent.guardrails(bad, cls)
    assert any("banned word" in p for p in probs)
    assert any("phone" in p for p in probs)


def test_pipeline_produces_approval_queue(tmp_path):
    src = tmp_path / "r.json"
    src.write_text(agent.Path("review_responder/sample_reviews.json").read_text())
    out = agent.run(str(src))
    assert len(out) == 4
    assert all(o["status"] in {"READY_FOR_APPROVAL", "NEEDS_HUMAN_EDIT", "ESCALATE"} for o in out)
    assert (tmp_path / "approval_queue.json").exists()


def test_mock_never_calls_network():
    llm = LLM(RunStats())
    assert llm.client is None
