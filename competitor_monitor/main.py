"""The run. `python -m competitor_monitor.main`: one invocation = one digest.

    MOCK_LLM=1 python -m competitor_monitor.main      # $0 demo: sample feed, fake model, prints a digest
    python -m competitor_monitor.main --seed          # before the first live run: remember today's items
    python -m competitor_monitor.main                 # a real run (DRY_RUN=1 prints instead of sending)
"""
from pathlib import Path
import difflib
import logging
import sys
import time

from . import agent, collect, config, deliver, store
from .llm import LLM, AgentDisabled
from .schemas import Item, RunStats

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("monitor")


def _page_changes(items: list[Item]) -> list[Item]:
    """Pricing and product pages: keep the first sighting as a baseline (no alert), and
    hand the model only the lines that changed since the last snapshot."""
    out = []
    for it in items:
        if it.page_text is None:
            out.append(it)
            continue
        prev = store.get_snapshot(it.url)
        store.save_snapshot(it.url, it.page_text)
        if prev is None:
            store.remember([it], {})            # baseline: seen, never alerted
            continue
        if prev == it.page_text:
            continue
        diff = "\n".join(l for l in difflib.unified_diff(prev.splitlines(), it.page_text.splitlines(), lineterm="", n=1)
                         if not l.startswith(("---", "+++")))
        out.append(it.model_copy(update={"text": ("Changes since the last check:\n" + diff)[:6000]}))
    return out


def seed(companies: list[dict] | None = None) -> int:
    """Remember everything that exists today without judging it, so day one does not
    spend the budget on old posts. Run once before the first scheduled run."""
    items = collect.collect_all(companies or config.load_companies())
    _page_changes(items)                         # stores page baselines
    store.remember([i for i in items if i.page_text is None], {})
    log.info("seeded %d items; nothing judged, nothing sent", len(items))
    return len(items)


def run(companies: list[dict] | None = None, items=None) -> RunStats:
    t0 = time.time()
    stats = RunStats()
    entries = []
    try:
        llm = LLM(stats)
        items = items if items is not None else collect.collect_all(companies or config.load_companies())
        stats.items_fetched = len(items)
        new = _page_changes(store.filter_new(items))
        stats.items_new = len(new)
        log.info("%d new of %d fetched", len(new), len(items))

        res = agent.run_agent(llm, new)
        if res.stopped:
            stats.errors.append(f"budget: {res.stopped}")
        entries = res.entries
        sent_hashes = {e.content_hash for e in entries}
        # remember what was judged and needs no digest line now; digest lines after delivery
        store.remember([i for i in new if i.content_hash in res.done - sent_hashes], res.judgements)

        outcome = deliver.send(entries)
        if outcome in ("sent", "printed"):
            store.remember([i for i in new if i.content_hash in sent_hashes], res.judgements)
        if outcome == "sent":
            store.mark_sent(list(sent_hashes))
        stats.items_sent = len(entries)
    except AgentDisabled as e:
        stats.errors.append(str(e))
        log.warning("%s", e)
    except Exception as e:  # noqa: BLE001
        stats.errors.append(str(e))
        log.exception("run failed")
    finally:
        store.log_run(stats, entries)
        log.info("done in %.1fs | calls=%d cost=$%.4f sent=%d errors=%s",
                 time.time() - t0, stats.llm_calls, stats.cost_usd, stats.items_sent, stats.errors)
    return stats


SAMPLE_FEED = Path(__file__).parent / "tests" / "fixtures" / "sample_feed.xml"

if __name__ == "__main__":
    if "--seed" in sys.argv:
        seed()
        sys.exit(0)
    demo = None
    if config.MOCK_LLM and "--live" not in sys.argv:     # offline demo: no network, no key, no spend
        demo = collect.from_rss("Acme", "blog", str(SAMPLE_FEED), client=None)
    s = run(items=demo)
    sys.exit(1 if s.errors else 0)
