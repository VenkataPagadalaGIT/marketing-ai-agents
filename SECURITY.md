# Security model

These agents read text they did not write: blog posts, news, pricing pages, reviews, crawl exports and briefs. Some of that text will one day contain instructions aimed at the model. The design assumes it.

## The lethal trifecta

An agent is dangerous when it has all three: it reads untrusted content, it can see private data, and it can send data out ([Simon Willison](https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/)). Remove any one leg and an injection cannot become a theft.

| Agent | Reads untrusted content | Sees private data | Can send data out | Why it is safe |
|---|---|---|---|---|
| Competitor monitor | Yes | No: only article text and a title reach the prompt | Only to the Slack webhook in config | The model cannot pick a destination; the worst case is an odd line in a digest a person reads |
| Review responder | Yes | No | No: drafts go to a local approval file | A person approves every public reply |
| Content brief | Yes | Brand facts (non-secret) | `fetch_page` only, limited to URLs its own SERP search returned | The model cannot invent a URL to exfiltrate data |
| Analytics narrator | Your own CSV | Client metrics | No | Output is a local file; numbers are checked against the data |
| Campaign orchestrator | Your own brief | The brief | No: an approval bundle on disk | Nothing publishes |
| SEO audit swarm | Crawl exports | No | No: a report file per client | Nothing publishes |

## Rules the code follows

- Secrets live in `.env` or a secret manager, never in prompts, logs or the repo.
- No tool takes a URL, email address or file path chosen by the model, except `fetch_page` in the content brief agent, which is limited to SERP results.
- Every prompt that receives outside text tells the model to treat it as data.
- Every model output is validated against a Pydantic schema before use.
- Public, paid or customer-facing actions always end in a human approval step.
- `AGENT_ENABLED=0` stops every model call.

Detectors and guard models are a second layer, never the first.

## Reporting a problem

Open a GitHub issue without exploit details and ask for a contact, or message the maintainer on [LinkedIn](https://www.linkedin.com/in/venkata-pagadala).
