# TransferPulse AI — How It Works (for traders)

*A plain-English guide to what the tool does. No code, no jargon — just the desk logic.*

---

## The one-sentence version

TransferPulse watches the insiders (Fabrizio Romano, Shams, etc.), and every time one of them tweets a transfer/free-agency rumour, it **opens or updates a "Next Club / Next Team" market on the player**, prices how hot the news is, and **pings the desk** when something is genuinely new, big, and from a source you'd trust.

Think of it as a junior analyst who never sleeps, reads every insider's feed, and taps you on the shoulder only when it's worth pricing.

---

## The key idea: one player = one market

This is the bit to internalise, because it's how the whole board is organised.

**A market is a player, not a destination.**

- "Álvarez to Arsenal", "Atlético won't sell Álvarez to Barcelona", "Chelsea make contact for Álvarez" — that's **three rumours in ONE market**: *Álvarez next club*.
- The clubs named in each rumour are just the **runners** in that player's market. They come and go; the player is the fixed line.

So the board isn't a list of "player X → club Y" bets. It's **one timeline per player**, and every new rumour about him drops onto his timeline in date order — no matter which club it mentions. That timeline (a market opening on the first whisper and building as the saga runs) is the whole product.

---

## The pipeline: three "agents" working a queue

Picture a betting slip moving down a conveyor belt through three desks. Each rumour ("post") goes through the same three steps.

```
  Insider feeds
       │
       ▼
 ┌───────────────┐   Reads the feeds, drops new posts into the queue.
 │ 1. COLLECTOR  │   You control WHICH sources it listens to.
 └───────────────┘
       │
       ▼
 ┌───────────────┐   "Is this even a transfer story? About whom?"
 │ 2. ASSESSMENT │   Turns a messy tweet into clean signal(s):
 └───────────────┘   player · sport · clubs · headline · summary.
       │
       ▼
 ┌───────────────┐   "How hot is this? Is it new? Do we escalate?"
 │ 3. TRADING    │   Prices it 0–100, decides Open/Update/Hold/Settle,
 │    IMPACT      │   and fires an alert if it's worth the desk's time.
 └───────────────┘
       │
       ▼
   The board — one timeline per player
```

### Agent 1 — The Collector (the runner who brings you the tips)
Pulls new posts off the insider feeds and puts them in the queue.

**Your lever:** in the sidebar you pick which sources to monitor, and you can see each one's **credibility tier**. Turn a source off and its posts never enter the queue at all — like muting a tipster you don't rate. (Great live demo moment: switch off a rubbish fan-aggregator account and watch the noise disappear from the board.)

### Agent 2 — The Assessment (the analyst who reads and files the story)
Takes one raw post and answers: **"Is this a transfer/free-agency story, and about which person?"**

- If it's a match report, an injury, banter, a "on this day" photo, a poll — **it's binned.** Nothing reaches the board. *Materiality is about scope, not drama:* a loud all-caps rumour still counts; a dramatic post that isn't about someone moving does not.
- If it IS a move story, it files a clean **signal**: the player (the market), the sport, the clubs linked, a factual headline, and a 1–2 line neutral summary you can read at a glance.
- One post can carry **two players** ("Álvarez to Arsenal, and separately Guimarães wants out") → it files **two** signals, one on each player's timeline.
- It also handles **managers/head coaches** moving jobs (including national-team appointments) as their own markets.

Crucially, it always spells the player's name the same way, so every rumour about him lands on the **same** timeline automatically.

### Agent 3 — Trading Impact & Alert (the desk analyst deciding what to escalate)
This is the pricing brain. For each new signal it looks at three things — **like you would**:

| It weighs… | In desk terms |
|---|---|
| **Novelty** | Is this actually new, or the fifth "still no bid" rehash? It's given the player's **existing timeline** so it can tell. Old news scores low *even if it's dramatic*. |
| **Credibility** | Who said it? It's handed the source's **tier** (1 = tier-1 insider whose word moves the line; 4 = unverified fan account). A tier-4 account screaming "DONE DEAL" in caps is treated as **noise**, however loud. |
| **Magnitude** | Does it move the whole market (free agency triggered, deal done, a decisive "we will NOT sell") or just add another interested name? |

It outputs:
- **Impact score 0–100** — how hot the signal is.
- **New info? yes/no** — does it add anything the timeline didn't already have.
- **One-line rationale** — *why* that score. (So you're never staring at a number with no reasoning.)
- **Suggested action** — the desk verb:
  - **Open market** → first credible rumour on a player. *Get a line up.*
  - **Update market** → new credible development on an open market. *Move your line.*
  - **Hold** → nothing new, or too weak/unverified to act on. *Sit tight.*
  - **Settle market** → deal confirmed and done. *Pay out / close.*

---

## When does it actually ping the desk? (the alert rule)

An alert fires **only when BOTH are true**:

1. **Impact score ≥ 70** (the threshold — adjustable), **and**
2. **It's genuinely new information.**

That's it. This is deliberate:

- A repeat of something already on the timeline → **no alert**, even from Fabrizio Romano himself. Being first-to-report the *same* thing again isn't tradable.
- A tier-4 aggregator shouting a huge rumour → **low score → no alert.** Loud ≠ credible.
- A dry, specific, credible new development ("Atlético will negotiate but won't sell to Barça") → **hot, new → alert.**

**Novelty gates everything.** The tool scores the *delta*, not the drama — exactly the discipline you'd apply before moving a line.

---

## What you see on screen (the demo)

- **🔔 Trading Alerts strip** (pinned up top) — the escalations, newest first: time · sport · market · one-line reason. This is "the tap on the shoulder."
- **KPIs across the top** — posts scanned · signals detected · markets open · alerts fired.
- **Tab 1 · Live Feed** — the raw queue draining in real time, each post ticking from ⏳ to ✅ as it's processed.
- **Tab 2 · Signals** — every filed signal as a table (date · headline · raw post · summary · clubs · impact+reason · market), newest first. Hotter rows are shaded stronger; alert rows carry a 🔔. Filter by sport, market, or alerts-only.
- **Tab 3 · Markets** — **the payoff.** One card per player, titled *"{player} next club/team"*, showing every club linked so far and the peak impact, with the **full chronological timeline** underneath. This is the saga view — a market being born on the first rumour and building event by event.

---

## Buttons you'll use in the demo

- **▶ Start / ⏸ Stop** — runs or pauses the conveyor belt. It processes one item per beat (~half a second) so it *looks alive* and you can narrate it. Stop and Start again resumes exactly where it left off — no double-counting.
- **Reset demo** — wipes the queue and board back to empty for a clean recording take.
- **Reprocess** (in Dev tools) — re-runs the SAME posts through Agents 2 & 3 from scratch without re-fetching. This is the tuning loop: tweak the analyst's instructions, reprocess, and watch the identical posts get re-scored. Runs are reproducible — the same posts always give the same signals and scores, so nothing drifts or doubles.

---

## Why this beats the desk reacting manually

Two edges, both about **time**:

1. **Open early.** The moment a credible rumour appears, the market's already opened and scored — hours or days before a human trawls the timeline.
2. **React fast.** As the saga develops, each new credible twist re-prices and re-alerts automatically, and each one lands on the right player's timeline with no manual sorting.

The tool's job isn't to replace your judgement — it's to make sure a tradable signal never sits unnoticed in someone's feed while the field gets on first.

---

## One thing it deliberately does NOT do

It has **no hard-coded answers.** It's never been told "Álvarez is a player" or "Romano is tier-1 in this specific case." It works out every player, club, and story purely from the post's words plus the general desk instructions and the source tier list you maintain. That's the point: it has to generalise to *tomorrow's* rumour about a player nobody's mentioned yet — not just replay a script.
