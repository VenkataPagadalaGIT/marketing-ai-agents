"""The run. `python -m competitor_monitor.main`: one invocation = one digest."""
import logging
import sys
import time

from . import agent, collect, config, deliver, store
from .llm import LLM, BudgetExceeded
from .schemas import RunStats

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("monitor")


def run(companies: list[dict] | None = None, items=None) -> RunStats:
    t0 = time.time()
    stats = RunStats()
    llm = LLM(stats)
    entries = []
    try:
        items = items if items is not None else collect.collect_all(companies or config.load_companies())
        stats.items_fetched = len(items)
        new = store.filter_new(items)
        stats.items_new = len(new)
        log.info("%d new of %d fetched", len(new), len(items))

        entries, judgements = agent.run_agent(llm, new)
        store.remember(new, judgements)

        if deliver.send(entries):
            store.mark_sent([e.url for e in entries])
        stats.items_sent = len(entries)
    except BudgetExceeded as e:
        stats.errors.append(f"budget: {e}")
        log.error("stopped: %s", e)
    except Exception as e:  # noqa: BLE001
        stats.errors.append(str(e))
        log.exception("run failed")
    finally:
        store.log_run(stats, entries)
        log.info("done in %.1fs | calls=%d cost=$%.4f sent=%d errors=%s",
                 time.time() - t0, stats.llm_calls, stats.cost_usd, stats.items_sent, stats.errors)
    return stats


if __name__ == "__main__":
    s = run()
    sys.exit(1 if s.errors else 0)
