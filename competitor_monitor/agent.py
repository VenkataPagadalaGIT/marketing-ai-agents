"""Steps 3 + 4: Judge and Summarise. The only steps that call a model."""
import logging

from . import config
from .llm import LLM
from .schemas import DigestEntry, Item, Judgement, Summary

log = logging.getLogger(__name__)

JUDGE_SYSTEM = """You screen competitor updates for a marketing manager.
Score how much this item deserves her attention today, 0-10:
 9-10 pricing change, major product launch, funding, acquisition, leadership change
 6-8  notable feature, partnership, big campaign, executive hire
 3-5  routine blog post, minor update, event recap
 0-2  irrelevant, spam, unrelated news mention
Use ONLY the text given. Return JSON: {"relevance": int, "category": "product|pricing|funding|hiring|content|partnership|other", "reason": "<=25 words"}"""

SUMMARY_SYSTEM = """You write one entry of a morning competitor digest for a marketing manager.
Rules: plain English, no hype, no speculation beyond the text, no adjectives like 'exciting'.
Return JSON: {"headline": "<=12 words", "summary": "1-2 sentences, what happened", "why_it_matters": "1 sentence, what she should consider doing"}"""


def judge(llm: LLM, item: Item) -> Judgement:
    user = f"Company: {item.company}\nSource: {item.source}\nTitle: {item.title}\nURL: {item.url}\n\n{item.text[:3000]}"
    return llm.structured(config.JUDGE_MODEL, JUDGE_SYSTEM, user, Judgement, max_tokens=120)


def summarise(llm: LLM, item: Item, j: Judgement) -> Summary:
    user = f"Company: {item.company} ({j.category}, relevance {j.relevance})\nTitle: {item.title}\n\n{item.text[:4000]}"
    return llm.structured(config.SUMMARY_MODEL, SUMMARY_SYSTEM, user, Summary, max_tokens=300)


def run_agent(llm: LLM, items: list[Item]) -> tuple[list[DigestEntry], dict[str, dict]]:
    """Judge everything; summarise only what clears the bar. Returns entries + all judgements."""
    judgements: dict[str, dict] = {}
    entries: list[DigestEntry] = []
    per_company: dict[str, int] = {}

    # Cheap pass over every item
    scored = []
    for it in items:
        try:
            j = judge(llm, it)
        except Exception as e:  # noqa: BLE001
            log.warning("judge failed for %s: %s", it.url, e)
            continue
        judgements[it.content_hash] = j.model_dump()
        scored.append((it, j))

    # Expensive pass over the best items only, capped per company
    for it, j in sorted(scored, key=lambda x: -x[1].relevance):
        if j.relevance < config.RELEVANCE_THRESHOLD:
            continue
        if per_company.get(it.company, 0) >= config.MAX_ITEMS_PER_COMPANY:
            continue
        s = summarise(llm, it, j)
        entries.append(DigestEntry(company=it.company, source=it.source, url=it.url, judgement=j, summary=s))
        per_company[it.company] = per_company.get(it.company, 0) + 1
    return entries, judgements
