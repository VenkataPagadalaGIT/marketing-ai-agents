import os
os.environ["MOCK_LLM"] = "1"

from analytics_narrator import agent  # noqa: E402

CSV = "analytics_narrator/sample_gsc.csv"


def test_analysis_is_deterministic():
    f = agent.analyze(CSV)
    assert f["week"] == "2026-W41" and f["totals"]["clicks"]["current"] == 1416
    assert f["totals"]["clicks"]["change_pct"] == 18.0
    movers = {m["page"]: m["delta"] for m in f["top_movers"]}
    assert movers["/pricing"] == 120 and movers["/blog/old-post"] == -90


def test_grounding_check_rejects_invented_numbers():
    f = agent.analyze(CSV)
    n = agent.Narrative(headline="Clicks up 18%", paragraphs=["Clicks reached 1,416, up from 1,200.", "Bounce rate fell to 37%."], actions=["x"])
    assert agent.ungrounded(n, f) == ["37"]        # 37 is not in the data → caught


def test_run_writes_markdown(tmp_path):
    import shutil
    p = tmp_path / "w.csv"
    shutil.copy(CSV, p)
    n = agent.run(str(p))
    assert n.headline and (tmp_path / "w.narrative.md").exists()


def test_grounding_check_covers_actions_too():
    f = agent.analyze(CSV)
    n = agent.Narrative(headline="Clicks up 18%", paragraphs=["Clicks reached 1,416."], actions=["Aim for 2,500 clicks next week"])
    assert agent.ungrounded(n, f) == ["2500"]


def test_bad_csv_header_is_rejected(tmp_path):
    p = tmp_path / "bad.csv"
    p.write_text("date,page,clicks\n2026-10-01,/,5\n")
    import pytest
    with pytest.raises(ValueError, match="missing columns"):
        agent.analyze(str(p))


def test_invented_number_skips_the_critic(monkeypatch, tmp_path):
    import shutil
    from common.llm import LLM
    p = tmp_path / "w.csv"
    shutil.copy(CSV, p)
    ex = agent.Narrative.model_config["json_schema_extra"]["example"]
    monkeypatch.setitem(ex, "paragraphs", ["Bounce rate fell to 37%."])
    calls = []
    real = LLM.structured
    monkeypatch.setattr(LLM, "structured", lambda self, *a, **k: calls.append(a[3].__name__) or real(self, *a, **k))
    agent.run(str(p))
    assert "Critique" not in calls and calls.count("Narrative") == agent.MAX_REVISIONS + 1