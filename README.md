# Marketing AI Agents: six production-shaped examples

Companion code for **The Complete Guide to AI Agents for Marketing**. Six agents, three difficulty levels,
one shared LLM client. Every agent runs locally with `MOCK_LLM=1` (no API key, no spend) and in production
through [OpenRouter](https://openrouter.ai) (any model, per-key spend caps, automatic fallbacks).

| # | Agent | Level | Pattern it teaches | Run |
|---|-------|-------|--------------------|-----|
| 1 | `competitor_monitor` | Beginner | Workflow + single judge/summarise agent, dedupe, budgets, scheduling | `python -m competitor_monitor.main` |
| 2 | `review_responder` | Beginner | Prompt chaining, code guardrails, human approval queue | `python -m review_responder.agent` |
| 3 | `content_brief` | Medium | Routing + tool-using agent loop (ReAct) + retrieval + step caps | `python -m content_brief.agent "keyword"` |
| 4 | `analytics_narrator` | Medium | Code-first analysis, evaluator-optimizer loop, grounding check | `python -m analytics_narrator.agent` |
| 5 | `campaign_orchestrator` | Complex | Orchestrator-workers, parallel specialists, shared state, checkpoints, approval gate | `python -m campaign_orchestrator.agent` |
| 6 | `seo_audit_swarm` | Complex | Fan-out/fan-in, routing handoffs, multi-tenant budgets, idempotent workers | `python -m seo_audit_swarm.agent` |

## Quick start

```bash
git clone https://github.com/VenkataPagadalaGIT/marketing-ai-agents && cd marketing-ai-agents
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest -q                      # 23 tests, all offline

MOCK_LLM=1 python -m competitor_monitor.main      # dry run, prints a digest, costs $0
```

## Going live

```bash
cp .env.example .env           # add OPENROUTER_API_KEY and SLACK_WEBHOOK_URL
MOCK_LLM=0 DRY_RUN=1 python -m competitor_monitor.main   # real models, still prints instead of sending
MOCK_LLM=0 DRY_RUN=0 python -m competitor_monitor.main   # sends to Slack
```

Scheduling: `.github/workflows/daily.yml` runs the monitor every weekday on GitHub Actions (free tier),
with tests gating the run and the SQLite state cached between runs.

## Layout

```
common/llm.py          one OpenRouter client: structured JSON, cost tracking, call/dollar caps, mock mode
competitor_monitor/    collect → dedupe → judge → summarise → deliver → remember
review_responder/      classify → draft → guardrails → approval queue
content_brief/         route → agent loop over tools → brief, with fixtures for offline runs
analytics_narrator/    analyse (code) → write → critique → fix, numbers checked against the CSV
campaign_orchestrator/ plan → specialists in parallel → review → human gate, checkpoint per stage
seo_audit_swarm/       per-tenant worker → parallel triage → specialist handoff → aggregate → critic
```

## Cost controls (same knobs everywhere)

| Env var | Default | Meaning |
|---|---|---|
| `CHEAP_MODEL` | `anthropic/claude-haiku-5.5` | Judging, classifying, critiquing |
| `STRONG_MODEL` | `anthropic/claude-sonnet-5.5` | Writing |
| `FALLBACK_MODELS` | `openai/gpt-5-mini,google/gemini-2.5-flash` | OpenRouter tries these if the primary fails |
| `MAX_LLM_CALLS_PER_RUN` | 60 | Hard stop |
| `MAX_COST_USD_PER_RUN` | 0.50 | Hard stop, uses OpenRouter's per-response cost |
| `MOCK_LLM` | 0 | 1 = never call the network |

Set a **per-key credit limit with a reset** on the OpenRouter key itself as the outer wall; the env caps are the inner wall.

## License

MIT. Model names are examples; swap for whatever is current on OpenRouter.

## Reading list

The guide's Part 19 lists the sources this repo stands on. The five to read first:
[Anthropic: Building effective agents](https://www.anthropic.com/research/building-effective-agents),
[OpenAI: A practical guide to building agents](https://cdn.openai.com/business-guides-and-resources/a-practical-guide-to-building-agents.pdf),
[ReAct](https://arxiv.org/abs/2210.03629),
[Why Do Multi-Agent LLM Systems Fail?](https://arxiv.org/abs/2503.13657),
[12-Factor Agents](https://github.com/humanlayer/12-factor-agents),
[The lethal trifecta](https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/).
