import os
os.environ["MOCK_LLM"] = "1"

from analytics_narrator import agent  # noqa: E402

CSV = "analytics_narrator/sample_gsc.csv"


def test_analysis_is_deterministic():
    f = agent.analyse(CSV)
    assert f["week"] == "2026-W41" and f["totals"]["clicks"]["current"] == 1416
    assert f["totals"]["clicks"]["change_pct"] == 18.0
    movers = {m["page"]: m["delta"] for m in f["top_movers"]}
    assert movers["/pricing"] == 120 and movers["/blog/old-post"] == -90


def test_grounding_check_rejects_invented_numbers():
    f = agent.analyse(CSV)
    n = agent.Narrative(headline="Clicks up 18%", paragraphs=["Clicks reached 1,416, up from 1,200.", "Bounce rate fell to 37%."], actions=["x"])
    assert agent.ungrounded(n, f) == ["37"]        # 37 is not in the data → caught


def test_run_writes_markdown(tmp_path):
    import shutil
    p = tmp_path / "w.csv"
    shutil.copy(CSV, p)
    n = agent.run(str(p))
    assert n.headline and (tmp_path / "w.narrative.md").exists()
