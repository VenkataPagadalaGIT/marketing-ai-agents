"""Step 5: Deliver. Code only. Quiet days send nothing."""
import logging
from collections import defaultdict
from datetime import date

import httpx

from . import config
from .schemas import DigestEntry

log = logging.getLogger(__name__)


def render_markdown(entries: list[DigestEntry]) -> str:
    if not entries:
        return ""
    by_co: dict[str, list[DigestEntry]] = defaultdict(list)
    for e in entries:
        by_co[e.company].append(e)
    lines = [f"*Competitor digest, {date.today():%a %d %b}*", ""]
    for company, es in by_co.items():
        lines.append(f"*{company}*")
        for e in es:
            lines.append(f"• <{e.url}|{e.summary.headline}>  _({e.judgement.category}, {e.judgement.relevance}/10)_")
            lines.append(f"   {e.summary.summary}")
            lines.append(f"   → {e.summary.why_it_matters}")
        lines.append("")
    return "\n".join(lines)


def send(entries: list[DigestEntry]) -> str:
    """Returns "quiet" (nothing to send), "printed" (dry run or no webhook) or "sent".
    The webhook URL comes from config only; the model never chooses where output goes."""
    body = render_markdown(entries)
    if not body:
        log.info("quiet day, nothing sent")
        return "quiet"
    if config.DRY_RUN or not config.SLACK_WEBHOOK_URL:
        print(body)
        return "printed"
    r = httpx.post(config.SLACK_WEBHOOK_URL, json={"text": body}, timeout=15)
    r.raise_for_status()
    return "sent"
