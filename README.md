# Probe

> Autonomous X/Twitter autopilot for the [@elxecutor](https://x.com/elxecutor) account — replies to posts, quotes, answers notifications, and publishes original content on a schedule, all under daily caps with deduplication.

`Probe` is a Python autopilot that runs as a scheduled GitHub Actions workflow. Each run posts at most one tweet, alternating between replies and quotes, and uses the [X Phoenix model](https://github.com/xai-org/x-algorithm) to rank candidates by predicted engagement before generating a response with an LLM.

## Features

- **Replies & quotes** — picks the best candidate from the home timeline using Phoenix engagement scoring (falls back to a heuristic when the model is off), generates a response with Groq, and runs it through safety and quality gates before posting.
- **Notification responses** — answers replies and mentions on your own posts, skipping anything you've already seen.
- **Original content** — turns trending EEE/hardware niche topics into original tweets.
- **Deduplication** — persistent `state.json` (stored as a workflow artifact) tracks every tweet replied to, quoted, or viewed so nothing is answered twice.
- **Daily caps** — configurable reply and quote limits per calendar day.
- **Muted-account aware** — candidates come from the home timeline, so muting an account on X is a real off-switch.
- **Notification floor** — stamp a baseline so the bot ignores your entire present backlog and only answers what arrives after.
- **CI gating** — a cheap preflight scan (no model load) decides whether each scheduled run even bothers downloading the Phoenix model and posting.
- **Dry-run mode** — preview every action without posting.

## Prerequisites

- Python 3.12+
- An X.com account with session cookies (`auth_token`, `ct0`)
- A [Groq API key](https://console.groq.com) (for generation + safety scoring)

## Quickstart

```bash
# 1. Clone and enter the repo
git clone https://github.com/elxecutor/probe.git && cd probe

# 2. Create and fill in your secrets
cp .env.example .env
# edit .env with your X cookies and Groq key

# 3. Install dependencies
pip install -r requirements.txt
```

> [!TIP]
> Get your X cookies from browser DevTools: Application → Cookies → `x.com`. You need `auth_token` and `ct0`.

### Run locally

```bash
# Preview a single autopilot cycle without posting
python engager.py --once --dry-run

# Post one real tweet (respects daily caps)
python engager.py --once

# Continuous loop (one cycle, then sleep and repeat)
python engager.py

# Just scan for fresh candidates (read-only, no model load)
python engager.py --preflight

# Print a manual digest of posts worth engaging with
python engager.py --digest

# Stamp the notification floor (ignore all present replies)
python engager.py --floor-notifications
```

## Configuration

All configuration lives in `.env` (see `.env.example` for the full list):

| Variable | Description | Default |
|---|---|---|
| `X_AUTH_TOKEN` | X session cookie `auth_token` | — |
| `X_CT0` | X CSRF cookie `ct0` | — |
| `X_USER_ID` | Your numeric X user id | — |
| `X_USERNAME` | Your X username | — |
| `GROQ_API_KEY` | Groq API key | — |
| `GROQ_MODEL` | Groq model for generation | `llama-3.3-70b-versatile` |
| `DAILY_REPLY_CAP` | Max replies per day | `20` |
| `DAILY_QUOTE_CAP` | Max quotes per day | `6` |

Command-line flags override caps for a single run: `--daily-reply-cap`, `--daily-quote-cap`, `--max-tweets`.

## How it works

```
┌──────────────┐     preflight      ┌─────────────────┐
│  GitHub      │───────────────────▶│  engager.py     │
│  Actions     │  (cheap scan)      │  --preflight    │
│  schedule    │                    └────────┬────────┘
└──────┬───────┘                             │ has candidates?
       │                                     ▼
       │                            ┌─────────────────┐
       │                            │  Download Phoenix│
       │                            │  model (~1.5GB) │
       │                            │  (cached in CI)  │
       │                            └────────┬────────┘
       │                                     ▼
       │                            ┌─────────────────┐
       │                            │  Gather + filter │
       │                            │  candidates      │
       │                            └────────┬────────┘
       │                                     ▼
       │                            ┌─────────────────┐
       │                            │  Rank by Phoenix │
       │                            │  engagement      │
       │                            └────────┬────────┘
       │                                     ▼
       │                            ┌─────────────────┐
       │                            │  Generate via    │
       │                            │  Groq + gate     │
       │                            └────────┬────────┘
       │                                     ▼
       │                            ┌─────────────────┐
       └───────────────────────────▶│  Post ONE tweet  │
                                   │  + save state    │
                                   └─────────────────┘
```

1. **Preflight** — a read-only scan of the home timeline checks if there are any fresh, unanswered candidates. If not, the run skips the expensive steps.
2. **Candidate gathering** — pulls from the home timeline (which excludes muted accounts) and applies freshness filters: per-account heartbeat, 48-hour window, dedup against `state.json`.
3. **Ranking** — scores candidates with the Phoenix engagement model, falling back to a raw-engagement heuristic when Phoenix is unavailable.
4. **Generation** — generates a reply or quote via Groq, then runs it through safety, honesty, and quality gates.
5. **Posting** — posts at most one tweet per run, updates `state.json`, and uploads it as a workflow artifact for the next run.

### Modes

`engager.py` subcommands:

| Command | Purpose |
|---|---|
| *(default)* | Continuous autopilot loop (one cycle per invocation) |
| `--once` | Run a single cycle and exit |
| `--preflight` | Cheap candidate scan, read-only (used as the CI gate) |
| `--digest` | Print a manual digest of posts worth engaging with |
| `--content` | Run the original-content cycle (trending niche topics) |
| `--floor-notifications` | Stamp the notification floor at the current time |

## CI/CD

The autopilot runs on a GitHub Actions schedule (every 30 minutes) defined in [`.github/workflows/run.yml`](.github/workflows/run.yml). State persists between runs via a workflow artifact (`autopilot-state`), and the Phoenix model is cached so only the first run pays the download.

Required repository secrets:

- `X_AUTH_TOKEN`, `X_CT0`, `X_USER_ID`, `X_USERNAME` — your X session cookies
- `GROQ_API_KEY` — your Groq API key

Manual runs are available from the Actions tab with two options:

- **Preview only** (`dry_run: true`) — preview actions without posting
- **Fresh state** — start from empty heartbeats (useful for testing or a reset)

> [!NOTE]
> The Phoenix ranker (~1.5 GB of embedding tables) is optional. When disabled, the autopilot falls back to a raw-engagement heuristic and skips the model download entirely. Flip `USE_PHOENIX` in the workflow to toggle.

## Project structure

```
.
├── engager.py          # CLI + autopilot loop + preflight + digest
├── engines.py          # Reply / quote / notification / content cycles
├── llm.py              # Groq helpers: generation, scoring, safety, niche checks
├── state.py            # Persistent state: dedup logs, daily caps, heartbeats
├── x_client.py         # X API client (GraphQL + REST)
├── phoenix_scorer.py   # Phoenix engagement model wrapper
├── requirements.txt
├── .env.example
└── .github/workflows/
    └── run.yml         # Scheduled autopilot (GitHub Actions)
```

## Privacy

Host this in a **private** repo. Nothing sensitive is committed (cookies and the Groq key live only in Actions secrets; `.env`, `state.json`, and logs are git-ignored), and draft text is stripped from the state artifact before upload. But on a public repo the run logs and state artifact are readable by anyone.
