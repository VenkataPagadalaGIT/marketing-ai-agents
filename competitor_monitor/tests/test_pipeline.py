"""Local test cases. Run `pytest`: no network, no API key, no spend.

Three layers:
  1. Unit:      each code step with a fixture
  2. Contract:  the LLM step with a mocked model, schema enforced
  3. End-to-end: a whole run in DRY_RUN + MOCK_LLM mode
"""
import os
from pathlib import Path

os.environ.update({"MOCK_LLM": "1", "DRY_RUN": "1"})  # before importing monitor

import pytest  # noqa: E402

from competitor_monitor import agent, collect, config, deliver, store  # noqa: E402
from competitor_monitor.llm import LLM, BudgetExceeded  # noqa: E402
from competitor_monitor.schemas import Item, Judgement, RunStats, Summary  # noqa: E402

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    p = str(tmp_path / "t.db")
    monkeypatch.setattr(config, "DB_PATH", p)
    return p


@pytest.fixture
def items():
    # feedparser accepts a file path, so the fixture stands in for a live feed
    return collect.from_rss("Acme", "blog", str(FIX / "sample_feed.xml"), client=None)


# ---- 1. unit -----------------------------------------------------------------
def test_collect_parses_feed(items):
    assert len(items) == 2
    assert items[0].company == "Acme" and items[0].source == "blog"
    assert "Pricing Plus" in items[0].title
    assert items[0].published_at is not None
    assert len(items[0].content_hash) == 64


def test_hash_is_stable_and_changes_with_content():
    a = collect.content_hash("u", "t", "body")
    assert a == collect.content_hash("u", "t", "body")
    assert a != collect.content_hash("u", "t", "body changed")


def test_dedupe_second_run_sees_nothing_new(tmp_db, items):
    assert len(store.filter_new(items, tmp_db)) == 2
    store.remember(items, {}, tmp_db)
    assert store.filter_new(items, tmp_db) == []


def test_render_quiet_day_is_empty():
    assert deliver.render_markdown([]) == ""


# ---- 2. contract -------------------------------------------------------------
def test_schema_rejects_bad_llm_output():
    with pytest.raises(Exception):
        Judgement.model_validate({"relevance": 42, "category": "product", "reason": "x"})
    with pytest.raises(Exception):
        Summary.model_validate({"headline": "h", "summary": "too short", "why_it_matters": "ok enough here"})


def test_judge_and_summarize_with_mock(items):
    llm = LLM(RunStats())
    j = agent.judge(llm, items[0])
    assert 0 <= j.relevance <= 10
    s = agent.summarize(llm, items[0], j)
    assert s.headline and s.why_it_matters
    assert llm.stats.llm_calls == 2


def test_budget_cap_stops_the_run(items, monkeypatch):
    monkeypatch.setattr(config, "MAX_LLM_CALLS_PER_RUN", 1)
    llm = LLM(RunStats())
    agent.judge(llm, items[0])
    with pytest.raises(BudgetExceeded):
        agent.judge(llm, items[1])


def test_per_company_cap(monkeypatch):
    monkeypatch.setattr(config, "MAX_ITEMS_PER_COMPANY", 1)
    many = [Item(company="Acme", source="blog", url=f"https://a/{i}", title=f"t{i}", text="x" * 400,
                 content_hash=str(i) * 64) for i in range(4)]
    res = agent.run_agent(LLM(RunStats()), many)
    assert len(res.judgements) == 4     # everything judged
    assert len(res.entries) == 1        # only one summarized


# ---- 3. end to end -----------------------------------------------------------
def test_full_run_dry(tmp_db, items, capsys):
    from competitor_monitor import main
    stats = main.run(items=items)
    assert stats.errors == []
    assert stats.items_new == 2
    assert stats.items_sent == 2
    out = capsys.readouterr().out
    assert "Competitor digest" in out and "Acme" in out

    # second run: nothing new, nothing printed
    stats2 = main.run(items=items)
    assert stats2.items_new == 0 and stats2.items_sent == 0


# ---- 4. behavior: the guarantees the guide makes ------------------------------
def test_budget_stop_keeps_unfinished_items_for_the_next_run(tmp_db, items, monkeypatch):
    from competitor_monitor import main
    monkeypatch.setattr(config, "MAX_LLM_CALLS_PER_RUN", 1)       # judge one item, then stop
    stats = main.run(items=items)
    assert any(e.startswith("budget:") for e in stats.errors)
    assert len(store.filter_new(items, tmp_db)) == 2               # nothing lost: both still new
    monkeypatch.setattr(config, "MAX_LLM_CALLS_PER_RUN", 40)
    stats = main.run(items=items)
    assert stats.errors == [] and stats.items_sent == 2


def test_sent_items_are_marked_sent(tmp_db, items, monkeypatch):
    import sqlite3
    from competitor_monitor import main
    monkeypatch.setattr(deliver, "send", lambda entries: "sent")
    main.run(items=items)
    sent = sqlite3.connect(tmp_db).execute("SELECT COUNT(*) FROM items WHERE sent=1").fetchone()[0]
    assert sent == 2


def test_pricing_page_first_sighting_is_baseline_then_diff(tmp_db):
    from competitor_monitor import main
    def page(text):
        return Item(company="Acme", source="pricing", url="https://acme.test/pricing", title="Acme pricing page",
                    text=text[:6000], content_hash=collect.content_hash("https://acme.test/pricing", text), page_text=text)
    v1 = "Starter $29 per month\nPro $79 per month\nEnterprise: contact us"
    assert main._page_changes([page(v1)]) == []                    # baseline, no alert
    changed = main._page_changes([page(v1.replace("$79", "$99"))])
    assert len(changed) == 1 and "+Pro $99 per month" in changed[0].text and "-Pro $79" in changed[0].text


def test_feed_hash_ignores_edits_to_the_post_body():
    assert collect.content_hash("u", "t") == collect.content_hash("u", "t")
    assert collect.content_hash("u", "page v1") != collect.content_hash("u", "page v2")


def test_kill_switch_stops_every_call(items, monkeypatch):
    from common import llm as shared
    monkeypatch.setattr(shared.config, "AGENT_ENABLED", False)
    with pytest.raises(shared.AgentDisabled):
        agent.judge(LLM(RunStats()), items[0])


def test_seed_remembers_without_judging(tmp_db, items, monkeypatch):
    from competitor_monitor import main
    monkeypatch.setattr(collect, "collect_all", lambda companies: items)
    assert main.seed(companies=[{"name": "Acme"}]) == 2
    assert store.filter_new(items, tmp_db) == []
