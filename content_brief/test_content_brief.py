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
    agent.run("best crm for small agencies")   # would loop forever without the cap
