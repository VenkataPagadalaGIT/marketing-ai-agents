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
    reports = asyncio.run(agent.run("seo_audit_swarm/sample_crawl.csv"))
    # all pages cached → only the critic call remains per tenant
    assert all(r["llm_calls"] == 1 for r in reports)


def test_tenant_page_cap(monkeypatch):
    _clean()
    monkeypatch.setitem(agent.TENANTS["acme-dental"], "max_pages", 2)
    reports = asyncio.run(agent.run("seo_audit_swarm/sample_crawl.csv"))
    assert {r["client"]: r["pages_audited"] for r in reports}["acme-dental"] == 2
