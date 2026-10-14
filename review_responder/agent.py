"""Use case 2 (beginner): Review Reply Drafter.

Pattern: PROMPT CHAINING + GUARDRAILS + HUMAN-IN-THE-LOOP.
  classify (cheap model) -> draft reply (strong model) -> code guardrails -> approval queue

Nothing is ever posted by this agent. It writes drafts to a queue; a person approves.
Run:  python -m review_responder.agent review_responder/sample_reviews.json
"""
import json
import re
import sys
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from common.llm import LLM, RunStats, config

BRAND = {
    "name": "Northside Dental",
    "voice": "warm, brief, first person plural, never defensive, never clinical jargon",
    "signoff": "- The Northside Dental team",
    "escalate_phone": "(404) 555-0100",
}
# Guardrail: words a reply must never contain (legal/medical promises, discounts we can't honour)
BANNED = ["guarantee", "refund", "free", "lawsuit", "malpractice", "diagnos", "prescri"]


class Classification(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {"sentiment": "negative", "issue": "wait_time", "needs_escalation": False, "mentions_staff": True}})
    sentiment: str = Field(pattern="^(positive|neutral|negative)$")
    issue: str = Field(pattern="^(wait_time|billing|staff|quality|facility|none|other)$")
    needs_escalation: bool          # legal threat, safety, discrimination → human handles the whole thing
    mentions_staff: bool


class Reply(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {"reply": "Thank you for letting us know about the wait. That is not the experience we want for you. We have shared this with our front desk and would welcome the chance to make it right; please call us so we can help directly.", "tone_check": "apologetic, specific, no promises"}})
    reply: str = Field(min_length=40, max_length=600)
    tone_check: str


CLASSIFY_SYSTEM = """Classify a customer review for a local business. Return JSON:
{"sentiment":"positive|neutral|negative","issue":"wait_time|billing|staff|quality|facility|none|other",
 "needs_escalation": bool (true for legal threats, safety incidents, discrimination, medical complaints),
 "mentions_staff": bool}"""

DRAFT_SYSTEM = f"""You draft public replies to online reviews for {BRAND['name']}.
Voice: {BRAND['voice']}.
Rules: 2-4 sentences. Thank them. Address the specific issue. Never promise refunds, discounts, or outcomes.
Never discuss health details publicly. For negative reviews invite them to call {BRAND['escalate_phone']}.
Do not include a sign-off; it is added by code. Return JSON {{"reply": "...", "tone_check": "<=10 words"}}"""


def guardrails(text: str, cls: Classification) -> list[str]:
    """Deterministic checks. Cheap, instant, and they catch what prompts miss."""
    problems = []
    low = text.lower()
    for w in BANNED:
        if w in low:
            problems.append(f"banned word: {w}")
    if cls.sentiment == "negative" and BRAND["escalate_phone"] not in text:
        problems.append("negative review reply must include the phone number")
    if re.search(r"\b\d{3}[-.]\d{3}[-.]\d{4}\b", text.replace(BRAND["escalate_phone"], "")):
        problems.append("unexpected phone number (PII?)")
    if len(text.split(".")) > 6:
        problems.append("too long")
    return problems


def process(llm: LLM, review: dict) -> dict:
    cls = llm.structured(config.CHEAP_MODEL, CLASSIFY_SYSTEM, review["text"], Classification, max_tokens=80)
    if cls.needs_escalation:
        return {"id": review["id"], "status": "ESCALATE", "classification": cls.model_dump(), "draft": None}

    draft, problems = None, ["not generated"]
    for attempt in range(2):                                   # retry once with the problems as feedback
        feedback = "" if not draft else f"\n\nPrevious draft had problems: {problems}. Fix them."
        user = f"Rating: {review['rating']}/5\nIssue: {cls.issue}\nReview: {review['text']}{feedback}"
        r = llm.structured(config.STRONG_MODEL, DRAFT_SYSTEM, user, Reply, max_tokens=300)
        draft = r.reply.strip() + "\n\n" + BRAND["signoff"]
        problems = guardrails(draft, cls)
        if not problems:
            break
    status = "READY_FOR_APPROVAL" if not problems else "NEEDS_HUMAN_EDIT"
    return {"id": review["id"], "status": status, "classification": cls.model_dump(), "draft": draft, "problems": problems}


def run(path: str) -> list[dict]:
    reviews = json.loads(Path(path).read_text())
    llm = LLM(RunStats())
    out = [process(llm, r) for r in reviews]
    queue = Path(path).with_name("approval_queue.json")
    queue.write_text(json.dumps(out, indent=2))
    print(f"{len(out)} reviews → {queue}  | calls={llm.stats.llm_calls} cost=${llm.stats.cost_usd:.4f}")
    for o in out:
        print(f"  [{o['status']}] #{o['id']} {o['classification']['sentiment']}/{o['classification']['issue']}")
    return out


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "review_responder/sample_reviews.json")
