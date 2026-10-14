import asyncio
import os
import shutil
os.environ["MOCK_LLM"] = "1"

from seo_audit_swarm import agent  # noqa: E402


def _clean():
    shutil.rmtree(agent.HERE / "cache", ignore_errors=True)


def test_swarm_audits_every_tenant_and_aggregates_in_code():
    _clean()
    reports = asyncio.run(agent.run("seo_audit_swarm/sample_crawl.csv"))
    by = {r["client"]: r for r in reports}
    assert by["acme-dental"]["pages_audited"] == 5 and by["bob-plumbing"]["pages_audited"] == 3
    assert sum(by["acme-dental"]["issue_counts"].values()) > 0
    assert by["acme-dental"]["top_fixes"][0]["fix"]["impact"] >= 1


def test_rerun_is_idempotent_and_free():
    _clean()
    asyncio.run(agent.run("seo_audit_swarm/sample_crawl.csv"))          # first run fills the cache
    reports = asyncio.run(agent.run("seo_audit_swarm/sample_crawl.csv"))
    # all pages cached → only the critic call remains per tenant
    assert all(r["llm_calls"] == 1 for r in reports)


def test_tenant_page_cap(monkeypatch):
    _clean()
    monkeypatch.setitem(agent.TENANTS["acme-dental"], "max_pages", 2)
    reports = asyncio.run(agent.run("seo_audit_swarm/sample_crawl.csv"))
    by = {r["client"]: r for r in reports}
    assert by["acme-dental"]["pages_audited"] == 2 and by["acme-dental"]["pages_dropped_by_cap"] == 3


def test_tenant_dollar_cap_skips_and_reports(monkeypatch):
    _clean()
    monkeypatch.setitem(agent.TENANTS["acme-dental"], "max_cost_usd", 0.0)   # no budget at all
    reports = asyncio.run(agent.run("seo_audit_swarm/sample_crawl.csv"))
    by = {r["client"]: r for r in reports}
    assert by["acme-dental"]["pages_skipped_for_budget"] == 5 and by["acme-dental"]["llm_calls"] == 0


def test_unknown_client_gets_default_caps():
    w = agent.Worker("new-client", [{"url": "/"}])
    assert w.cfg == agent.DEFAULT_TENANT


def test_fixes_rank_by_impact_then_low_effort_first():
    assert sorted(["high", "low", "medium"], key=agent.EFFORT.get) == ["low", "medium", "high"]
