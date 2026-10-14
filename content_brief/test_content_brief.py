import os
os.environ["MOCK_LLM"] = "1"

from content_brief import agent  # noqa: E402


def test_tools_return_trimmed_data():
    serp = agent.search_serp("best crm for small agencies")
    assert 1 <= len(serp) <= 10 and {"pos", "title", "url"} <= set(serp[0])
    page = agent.fetch_page(serp[0]["url"])
    assert "h2s" in page and "html" not in page


def test_run_produces_valid_brief_and_stops():
    brief = agent.run("best crm for small agencies")
    assert brief.primary_keyword.lower().startswith("best crm")
    assert len(brief.outline) >= 4 and brief.sources


def test_step_cap_holds(monkeypatch):
    # force the mock to never finish: the loop must still end at MAX_STEPS
    monkeypatch.setitem(agent.Action.model_config["json_schema_extra"]["example"],
                        "tool", "keyword_data")
    monkeypatch.setitem(agent.Action.model_config["json_schema_extra"]["example"],
                        "args", {"keyword": "x"})
    monkeypatch.setattr(agent, "MAX_STEPS", 3)
    from common.llm import LLM
    calls = []
    real = LLM.structured
    monkeypatch.setattr(LLM, "structured", lambda self, *a, **k: calls.append(a[3].__name__) or real(self, *a, **k))
    agent.run("best crm for small agencies")   # without MAX_STEPS only the run's call cap would stop it
    assert calls.count("Action") == 3          # exactly MAX_STEPS loop steps, then the brief


def test_fetch_page_only_accepts_urls_from_the_serp(monkeypatch):
    ex = agent.Action.model_config["json_schema_extra"]["example"]
    monkeypatch.setitem(ex, "tool", "fetch_page")
    monkeypatch.setitem(ex, "args", {"url": "https://attacker.example/steal?data=secret"})
    monkeypatch.setattr(agent, "MAX_STEPS", 2)
    seen = []
    real = agent.fetch_page
    monkeypatch.setitem(agent.TOOLS, "fetch_page", lambda **kw: seen.append(kw) or real(**kw))
    agent.run("best crm for small agencies")
    assert seen == []                          # refused: that URL never came from search_serp
