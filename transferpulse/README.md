# 📡 TransferPulse AI

Real-time transfer & free-agency intelligence — spot and price **Next Club /
Next Team** markets before the field.

TransferPulse watches transfer sources across **football and the NBA**, detects
transfer / free-agency signals, groups every rumour about a player into a single
**market**, and for each new rumour decides whether it adds new information,
scores how hot it is, and **raises an alert to the trading desk** when a signal
is hot, new and credible.

> Prototype for a hackathon demo. Optimised to run reliably on first run, look
> good on screen and be explainable in three minutes — not for scale, auth or
> production robustness.

## Run it

```bash
pip install -r requirements.txt
# put your key in .env  (cp .env.example .env, then paste it)
streamlit run app.py
```

Press **▶ Start**. The feed fills, the queue drains, signals appear and scores
fill in as a visible flow. **♻ Reset demo** returns to a clean slate.

## How it works

```
Agent 1 (Collector)     ─▶ data/raw_posts.csv (the queue, processed = 0/1)
Agent 2 (Assessment)    ─▶ 0+ signals per post → one events.csv row per player
Agent 3 (Trading Impact)─▶ new info? · impact score · alert → fills those rows
                        ─▶ Markets board: one timeline per player
```

- **A market is a player.** Every rumour about a player — whichever club is
  named — lands on the same timeline. The clubs are just the *runners*, stored
  in a `clubs` column. Grouping is `events.groupby(normalise(player))`; no
  matching logic, no buckets table.
- **Agent 2** turns one post into a list of signals (zero for a match report,
  a manager move, nostalgia; two if a post carries two players). Structured
  output via a Pydantic schema — the shape is enforced, not parsed.
- **Agent 3** scores the *delta, not the drama*: novelty × source credibility ×
  magnitude. A repeat scores low even from a tier-1 insider; a tier-4 "DONE
  DEAL" scores low however loud. An alert fires only when the score clears
  `ALERT_THRESHOLD` **and** the signal is new.

## Files

| Path | Role |
|---|---|
| `app.py` | Streamlit UI + orchestrator. **Owns all CSV writes.** |
| `config.py` | Model, cadence, alert threshold, paths. Nothing else. |
| `store.py` | All CSV read/write; `normalise`, `reset_all`, `reprocess_all`. |
| `agents/collector.py` | Agent 1 — release fixture posts from selected sources. |
| `agents/assessor.py` | Agent 2 — post → list of player signals. |
| `agents/trading_impact.py` | Agent 3 — novelty + credibility + magnitude → alert. |
| `config/sources.csv` | **Provided, read-only.** Credibility tiers (1 best, 4 worst). |
| `fixtures/posts.csv` | **Provided, read-only.** 30-post source Agent 1 releases from. |
| `data/raw_posts.csv` | Live queue (generated; wiped by Reset). |
| `data/events.csv` | Signals / board (generated; wiped by Reset). |

The agents derive everything from post content plus general instructions — no
player, club, team or handle is hard-coded anywhere outside `fixtures/`.

## Demo tips

- Turn off a low-credibility (tier-4) source in the sidebar before Start to show
  its noise never reaching the board.
- **Dev tools → Reprocess** re-runs Agents 2 + 3 on the same posts without
  re-fetching — the prompt-tuning loop. Event count stays identical, never
  doubles.
