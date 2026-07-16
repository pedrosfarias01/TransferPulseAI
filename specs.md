## Context

Build **TransferPulse AI**, a prototype for an internal hackathon at a sports betting company.

**The opportunity:** "Next Club" / "Next Team" markets — *Where will Julián Álvarez sign? Where does LeBron James play his 24th season?* — are huge-volume betting markets across **both football and the NBA**. Their value depends on two things the book is slow at today: **opening the market early**, the moment a credible rumour appears, and **reacting** as the story develops. Both signals live on social media — journalists like Fabrizio Romano (football) and Shams Charania (NBA) — hours or days before the trading desk reacts.

TransferPulse watches those sources, detects transfer/free-agency signals across football and the NBA, groups every rumour about a player into a single **market**, and for each new rumour decides whether it actually adds new information, scores how hot it is, and **raises an alert to the trading desk** when a signal is hot, new and credible.

**This is a prototype for a demo video.** Optimise for: works reliably on first run, looks good on screen, easy to explain in 3 minutes. Do **not** optimise for scale, auth, tests, or production robustness. Keep it simple and clean. Prefer the boring solution everywhere.

---

## Stack

- Python 3.11+
- **Streamlit** — single page, the whole demo runs here
- **CSV files as the database**, via pandas. No SQL.
- **OpenAI library** for the two LLM agents (`OPENAI_API_KEY` from `.env`; model string in `config.py`)
- **Pydantic** for LLM output schemas

No frontend framework, no Docker, no agent framework (no LangChain).

**Structured output:** get JSON from the model with `client.chat.completions.parse(...)` passing the Pydantic model directly (or, failing that, `response_format={"type": "json_schema", ...}`). Do **not** prompt for JSON and then `json.loads` the free text — that's the part most likely to break, especially for Agent 2, whose response is a list of variable length. Let the schema enforce the shape.

`config.py` holds, and nothing else does: `MODEL`, `FETCH_BATCH_SIZE`, `TICK_SLEEP_SECONDS`, `ALERT_THRESHOLD` (impact score at/above which an alert fires), and the data/fixtures paths.

---

## Architecture

```
Agent 1 (Collector)   ──▶  raw_posts.csv  (processed = 0/1)   ← the queue
                              │
                              ▼
Agent 2 (Assessment)  ──▶  0+ signals, each: sport · player · clubs · summary
                              │            (writes one events.csv row per player)
                              ▼
Agent 3 (Trading Impact) ─▶  new info? · impact score · triggers alert
                              │            (fills the impact columns on that row)
                              ▼
                          events.csv  ──▶  Board: one timeline per player
```

Each agent is a plain Python module with one entry function. **events.csv is the hand-off between Agent 2 and Agent 3**: Agent 2 appends a row per detected signal with the impact columns left empty; Agent 3 later reads the unpriced rows and fills them.

---

## The core model: a market is a player, the board is a timeline

A **market** is "**{player} next club/team**". Every rumour about that player — whichever club or franchise is mentioned — belongs to the **same** market. The clubs named in a post are just the **runners** inside it, recorded in a `clubs` column.

The grouping key is just the player:

```python
story_key = normalise(player)      # "julian alvarez", "lebron james"
```

`normalise` = trim, lowercase, strip accents, so "Álvarez"/"Alvarez" don't split one market in two. The board does `events.groupby("story_key")` and renders each group as **one market with a chronological timeline of events** — this timeline is the centrepiece of the product. Because the bucket is simply the player, grouping is trivial and needs no matching logic: no buckets table, no LLM matching step, no tag in the key. If Agent 2 names the player consistently, every rumour lands on the right timeline automatically.

This is deliberately different from keying on the destination: "Atlético won't sell Álvarez to Barcelona" and "Arsenal make contact for Álvarez" are the **same market** (Álvarez); the clubs differ but the player doesn't. That is the whole point.

---

## Storage (`store.py`)

A thin helper so every CSV read/write happens in one place. Keep it small.

- All reads/writes use pandas with `encoding="utf-8"`. The provided fixtures carry a BOM, so read those with `encoding="utf-8-sig"`; post content has emoji, so never rely on the platform default (it corrupts them on Windows).
- `next_id(table)` → max existing id + 1.
- `reset_all()` → clear `data/raw_posts.csv` and `data/events.csv` back to headers; never touch `fixtures/`.
- `reprocess_all()` → set every `raw_posts.processed = 0` and clear `events.csv` (so a re-run doesn't append duplicate rows).
- Single-threaded: everything runs inside Streamlit's rerun loop — never a background thread.

### `fixtures/sources.csv` — **PROVIDED, read-only**
`handle, url, credibility_tier, credibility_note, enabled`

Nine accounts across football and the NBA, each with a credibility tier (1 = tier-1 insider, 4 = unverified fan aggregator). The tier list is maintained by the trading desk, not guessed by the model — it is passed into Agent 3. If a post ever comes from a handle not in this file, default it to tier 3.

### `data/raw_posts.csv` — the queue (Agent 2's input)
`id, source_handle, post_url, author, posted_at, raw_content, fetched_at, processed`

### `data/events.csv` — the signals (Agent 2 writes, Agent 3 enriches)
`id, raw_post_id, event_date, sport, headline, raw_content, summary_llm, player, story_key, clubs, is_new_information, impact_score, impact_rationale, suggested_action, alert`

Every row is a transfer signal (that's all that ever gets written here). Agent 2 fills everything up to `clubs`; the impact columns (`is_new_information` onward) start empty and are filled by Agent 3 — an empty `impact_score` means "not yet priced". `clubs` is a simple `"Arsenal; Barcelona"` string.

The Signals tab must display exactly these columns, in this order:

`DATE | HEADLINE | RAW_CONTENT | SUMMARY_LLM | CLUBS | IMPACT_LLM | MARKET`

…where `IMPACT_LLM` renders as `"{impact_score}{ 🔔 if alert} — {impact_rationale}"` and `MARKET` is `"{player} next club"` (football) or `"{player} next team"` (NBA), chosen from `sport`. Keep the granular columns in the CSV; compose only for display.

---

## Agent 1 — Collector (`agents/collector.py`)

Appends posts into `raw_posts.csv` with `processed = 0`. Dedupe on `post_url`.

**Only release posts from the sources currently selected in the sidebar.** The multiselect must actually filter the collector — deselecting a source means its posts never enter the queue. (A live demo interaction: turning off a low-credibility source visibly changes what reaches the board.)

It reads the **provided** `fixtures/posts.csv` and releases the next few unreleased rows on each call, stamping `fetched_at` at release time. Track released rows by which ids already exist in `raw_posts.csv`, so Start/Stop/resume never double-inserts.

No live scraping for now — it's the fragile part and the demo doesn't need it. Keep the read behind a single `fetch_new_posts()` function so a live adapter could be slotted in later without touching the rest of the pipeline, but do not build one now.

---

## Fixtures — **PROVIDED, do not generate**

`fixtures/posts.csv` is given: 30 rows, chronological, exactly the `raw_posts` schema, all `processed = 0`, mixing football and NBA. **Use it as-is. Do not invent, rewrite, extend or "improve" the posts.** `post_url` values are placeholders — don't validate URLs.

**Do not special-case any post, player, club, team or source.** No hard-coded names, handles, market titles, clubs or scores anywhere in the code or the LLM prompts. The agents derive everything from post content plus the general instructions. The fixtures are held out as a test of exactly that — a pipeline that only works because it was taught the answers is worthless.

---

## Agent 2 — Assessment (`agents/assessor.py`)

Input: one unprocessed post. **One** LLM call, structured JSON out, Pydantic-validated.

A single post can carry news about **more than one player** (e.g. "Álvarez to Arsenal, and separately Guimarães wants out"). So Agent 2 returns a **list**, one element per player the post carries a transfer signal about:

```json
{ "signals": [
    { "player": "...", "sport": "Football|Basketball", "clubs": ["...","..."],
      "headline": "...", "summary": "..." }
] }
```

- Empty `signals` list = the post is **not** a transfer signal. Nothing is written; the post is just marked processed. This is how match reports, injuries, a *coach/manager* changing jobs, polls, banter, nostalgia and photos are filtered — **materiality is about scope, not drama** (a tier-1 "here we go" about a manager is still not a player next-club/team signal).
- `player` — full name, no nicknames; consistent across posts so the market key is stable. This is the market.
- `clubs` — every club/franchise the post links this player with (may be one, several, or empty for a vague "wants to leave").
- `summary` — 1–2 sentences, neutral, trader-readable.
- `headline` — factual, ≤ 12 words.

The orchestrator writes **one events.csv row per element** (impact columns empty), computing `story_key = normalise(player)` in Python and storing `clubs` as a `"; "`-joined string. Never ask the model for the story_key.

Be explicit in the system prompt: **the player is the market; the clubs are only the runners** — two posts about the same player naming different clubs are the same market, never a new one.

---

## Agent 3 — Trading Impact & Alert (`agents/trading_impact.py`)

Reads an unpriced row from `events.csv` (empty `impact_score`) and fills its impact columns. Input: that event, **plus**:
- **the market's existing timeline** — the already-priced events with the same `story_key`, chronologically. This is what lets it judge novelty.
- the **credibility tier and note** for the source, from `sources.csv`.

**One** LLM call, structured JSON out:
- `is_new_information` — boolean. Does this add something the market's timeline doesn't already contain? A repeat, rehash or weaker echo is `false`.
- `impact_score` — 0–100: how **hot** the signal is. Combine three things — **novelty** (old news scores low even if dramatic), **source credibility** (a tier-4 aggregator scores low however loud), and **magnitude** (does it shift the whole market, or just add a name?).
- `impact_rationale` — one sentence: why this score.
- `suggested_action` — `Open market | Update market | Hold | Settle market`.
- `alert` — boolean. **True when `impact_score >= ALERT_THRESHOLD` and `is_new_information` is true.** The signal escalated to the trading desk; it should fire for genuinely market-moving posts and stay quiet for repeats and low-credibility noise.

Frame the system prompt as a **trading analyst deciding what to escalate to the desk**, and tell it:

- **Score the delta, not the drama.** A dry "Atlético will not sell to Barcelona" is hot and new; the fifth "still no bid" post is neither. A loud all-caps rumour from a tier-4 account is low impact and must not alert.
- **Novelty gates the alert.** Re-reporting something already on the timeline is `is_new_information = false`, low impact, no alert — even from a tier-1 source.
- **First credible rumour in a market → `Open market`. A confirmed completed deal → `Settle market`.**

On success: write the impact columns, mark the source raw_post `processed = 1` if not already. On failure: leave the row unpriced to retry, and surface the error in the UI.

---

## Streamlit app (`app.py`)

Wide layout, dark-friendly. Title **TransferPulse AI**; subtitle: real-time transfer & free-agency intelligence — spot and price Next Club / Next Team markets before the field.

**Sidebar**
- Multiselect of sources to monitor, showing each one's sport and credibility tier. **This filters Agent 1.**
- **▶ Start** / **⏸ Stop**
- **Reset demo** → `store.reset_all()`. Empties queue and board; Agent 1 re-fetches on next Start. Pressed before recording.
- `st.expander("Dev tools")`, collapsed by default so it stays out of frame:
  - **Reprocess** → `store.reprocess_all()`. Same posts, board cleared, re-runs **Agent 2 + Agent 3** from scratch; Agent 1 skipped. The tuning loop: tweak a prompt, Reprocess, Start, re-score identical posts without re-fetching.

**🔔 Trading Alerts** — a strip pinned **above the tabs**, listing fired alerts newest-first (time · sport · market · one-line rationale). This is the "alert to the trading desk". Empty state: "No alerts yet." When an alert fires mid-run, also flash it in the live status line.

**Pipeline loop — make it look alive**

`st.session_state.running` is the flag. Each rerun does **exactly one unit of work**, then `st.rerun()`, in this priority:
1. Raw queue has fewer than 3 unprocessed posts → run Agent 1, fetch a small batch from the **selected sources**.
2. Else, an events row is unpriced (empty `impact_score`) → run **Agent 3** on the oldest one; if it alerts, push to the alerts strip.
3. Else, a raw post is unprocessed → run **Agent 2** on the oldest; append one events row per signal (or none), then mark it processed.

This ordering means each post is assessed and then priced a tick later, so the feed drains, signals appear, and scores fill in as a visible flow. `time.sleep(0.5)` so a human can follow it. Live status line — `"Agent 2: reading @ShamsCharania…"` → `"Agent 3: scoring LeBron James…"` → `"🔔 ALERT · LeBron James (84)"`. Stop on button press, or when the queue is empty and every event is priced.

**`app.py` owns all writes.** Agents return data; only the orchestrator writes to the CSVs. On an LLM error, leave the work undone (post unprocessed, or event unpriced) so it retries next tick, and surface the error.

**KPIs across the top:** posts scanned · signals detected · markets open · alerts fired

**Three tabs**
1. **Live Feed** — `raw_posts` with a Processed ✅/⏳ column. The queue, visibly draining. A small Football/NBA badge per row.
2. **Signals** — the `DATE | HEADLINE | RAW_CONTENT | SUMMARY_LLM | CLUBS | IMPACT_LLM | MARKET` table, newest first. Shade rows by `impact_score` (hotter = stronger); mark alert rows with 🔔. Filters: sport, market, alerts-only.
3. **Markets** — the payoff. `groupby("story_key")`, sorted by most recent activity, badged by sport. One expandable card per market titled **"{player} next club/team"**, showing the **clubs linked so far** (deduped from the `clubs` column across the timeline) and the current peak impact, and below it the **chronological event timeline** (time → headline → summary → impact chip → 🔔 if alerted → action). This timeline — a market opening on the first rumour and building as the saga develops — is the core deliverable.

---

## Repo layout

```
transferpulse/
├── app.py
├── config.py
├── store.py
├── agents/
│   ├── collector.py
│   ├── assessor.py
│   └── trading_impact.py
├── config/
│   └── sources.csv       # PROVIDED — read-only, do not regenerate
├── data/                 # generated at runtime; wiped by Reset; gitignored
│   ├── raw_posts.csv     # PROVIDED — read-only, do not regenerate
│   └── events.csv
├── requirements.txt
```

---

## Acceptance criteria

1. `pip install -r requirements.txt && streamlit run app.py` works from a clean checkout.
2. CSVs auto-create on first run; provided files are read, not overwritten.
3. **Start** fills the feed, drains the queue and populates the board with no manual steps; all 30 fixture posts are assessed, and every resulting signal is priced, exactly once.
4. Every rumour about the same player lands in one market timeline, in chronological order, regardless of which clubs it names — for both a football and an NBA player.
5. A post that carries signals about two different players produces **two** events rows, one per player, each in the correct market.
6. The `clubs` column is populated with the clubs a post links the player to; a market card lists all clubs seen across its timeline.
7. `impact_score` reflects novelty **and** credibility: a repeat of existing timeline info scores low even from a tier-1 source; a tier-4 "DONE DEAL" scores low however loud. Rationales say why.
8. Alerts fire only for hot, new, credible signals — not for repeats or aggregator noise. The alerts strip matches the 🔔 rows in Signals.
9. Non-transfer posts (match/Summer-League report, manager/coach move, poll, nostalgia, photo) produce zero signals and never reach `events.csv`.
10. Emoji renders correctly throughout — no mojibake.
11. **Stop** halts mid-run; **Start** resumes without duplicating work; **Reset demo** returns to a clean state.
12. **Reprocess → Start** re-scores the same posts and produces the **same number of events as before, not double**. Run it twice; event count and timelines identical.
13. Grep the code for any player, club, team or handle from the fixtures. Zero matches outside `fixtures/`. A hit in `agents/` means the answers were hard-coded.

Build it end to end, then tell me what you had to change, and what you'd cut if we had half the time.