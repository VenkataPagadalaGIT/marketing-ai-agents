import os
os.environ["MOCK_LLM"] = "1"

from common.llm import LLM, RunStats  # noqa: E402
from review_responder import agent  # noqa: E402


def test_guardrails_catch_promises_and_missing_phone():
    cls = agent.Classification(sentiment="negative", issue="billing", needs_escalation=False, mentions_staff=False)
    bad = "We guarantee a full refund and a free visit."
    probs = agent.guardrails(bad, cls)
    assert any("banned phrase" in p for p in probs)
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


def test_guardrails_allow_ordinary_phrases():
    cls = agent.Classification(sentiment="positive", issue="none", needs_escalation=False, mentions_staff=True)
    ok = "Thank you! Feel free to call us any time; we are glad the visit went well."
    assert agent.guardrails(ok, cls) == []


def test_guardrails_catch_pii_and_names():
    cls = agent.Classification(sentiment="positive", issue="none", needs_escalation=False, mentions_staff=False)
    text = "Thanks Maria! Email us at front@example.com or call 404.555.0199."
    probs = agent.guardrails(text, cls, author="Maria Lopez")
    assert any("email" in p for p in probs) and any("phone" in p for p in probs) and any("name" in p for p in probs)


def test_escalations_get_no_draft():
    llm = LLM(RunStats(), mock={"_default": True})
    agent.Classification.model_config["json_schema_extra"]["example"]["needs_escalation"] = True
    try:
        out = agent.process(llm, {"id": 1, "rating": 1, "text": "I'm talking to a lawyer."})
    finally:
        agent.Classification.model_config["json_schema_extra"]["example"]["needs_escalation"] = False
    assert out["status"] == "ESCALATE" and out["draft"] is None and llm.stats.llm_calls == 1
