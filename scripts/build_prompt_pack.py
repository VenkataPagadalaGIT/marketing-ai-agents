"""Regenerate the "In the repo now" prompts in prompts/README.md from the code.

    MOCK_LLM=1 python scripts/build_prompt_pack.py          # rewrite
    MOCK_LLM=1 python scripts/build_prompt_pack.py --check  # exit 1 if the pack is stale (CI)
"""
import os
import re
import sys
from pathlib import Path

os.environ.setdefault("MOCK_LLM", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analytics_narrator import agent as an  # noqa: E402
from campaign_orchestrator import agent as co  # noqa: E402
from competitor_monitor import agent as cm  # noqa: E402
from content_brief import agent as cb  # noqa: E402
from review_responder import agent as rr  # noqa: E402
from seo_audit_swarm import agent as sw  # noqa: E402

PACK = Path(__file__).resolve().parent.parent / "prompts" / "README.md"
VALUES = [cm.JUDGE_SYSTEM, cm.SUMMARY_SYSTEM, rr.CLASSIFY_SYSTEM, rr.DRAFT_SYSTEM, cb.ROUTER_SYSTEM, cb.AGENT_SYSTEM,
          cb.BRIEF_SYSTEM, an.WRITER, an.CRITIC, co.MANAGER,
          "\n".join(f"[{k}] {v}{co.SPECIALIST_SUFFIX}" for k, v in co.SPECIALIST.items()),
          co.REVIEWER, sw.TRIAGE, "\n".join(f"[{k}] {v}{sw.SUFFIX}" for k, v in sw.SPECIALIST.items()), sw.CRITIC]


def build(text: str) -> str:
    parts = re.split(r"(\*\*In the repo now\*\*[^\n]*\n\n```text\n)(.*?)(\n```)", text, flags=re.S)
    n = (len(parts) - 1) // 4
    if n != len(VALUES):
        raise SystemExit(f"expected {len(VALUES)} prompt blocks, found {n}")
    out = [parts[0]]
    for i in range(n):
        head, _, tail, rest = parts[1 + 4 * i: 5 + 4 * i]
        out += [head, VALUES[i].strip(), tail, rest]
    return "".join(out)


if __name__ == "__main__":
    current = PACK.read_text()
    fresh = build(current)
    if "--check" in sys.argv:
        if fresh != current:
            print("prompts/README.md is stale: run MOCK_LLM=1 python scripts/build_prompt_pack.py")
            sys.exit(1)
        print("prompt pack matches the code")
    else:
        PACK.write_text(fresh)
        print(f"wrote {PACK}")
