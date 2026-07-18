"""TransferPulse AI — Streamlit app.

Real-time transfer & free-agency intelligence: spot and price Next Club /
Next Team markets before the field.

This module owns ALL writes to the CSVs. The agents return data; only this
orchestrator persists it. Each Streamlit rerun does exactly one unit of
pipeline work, then reruns, so the feed drains and scores fill in as a visible
flow.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone

import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from openai import OpenAI

import config
import notifier
import store
from agents import collector
from agents.assessor import assess_post
from agents.trading_impact import score_event

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
load_dotenv()

st.set_page_config(
    page_title="TransferPulse AI",
    page_icon="📡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Dark-friendly polish. Streamlit theming is respected; these are light-touch
# accents so the app reads well on a dark background on screen.
st.markdown(
    """
    <style>
      .block-container { padding-top: 1.5rem; }
      .tp-alert {
        border-left: 4px solid #ff4b4b; background: rgba(255,75,75,0.08);
        padding: 8px 12px; margin-bottom: 6px; border-radius: 4px;
        font-size: 0.9rem;
      }
      .tp-alert .market { font-weight: 700; }
      .tp-status {
        font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
        padding: 6px 10px; border-radius: 4px; background: rgba(128,128,128,0.12);
      }
      .tp-badge {
        display: inline-block; padding: 1px 8px; border-radius: 10px;
        font-size: 0.72rem; font-weight: 700; margin-right: 6px;
      }
      .tp-badge.football { background: #1e7e34; color: #fff; }
      .tp-badge.basketball { background: #c05600; color: #fff; }
      .tp-badge.mixed { background: #555; color: #fff; }
      .tp-badge.role-player { background: #2c5f8a; color: #fff; }
      .tp-badge.role-mgr { background: #6a3d9a; color: #fff; }
      .tp-raw {
        color: #9aa; font-size: 0.8rem; font-style: italic;
        border-left: 2px solid rgba(128,128,128,0.3); padding-left: 8px;
        margin-top: 4px;
      }
      .tp-chip {
        display: inline-block; padding: 1px 8px; border-radius: 10px;
        font-size: 0.75rem; font-weight: 700; color: #fff;
      }
      .tp-clublist { color: #bbb; font-size: 0.85rem; }
      .tp-tl { border-left: 2px solid rgba(128,128,128,0.4);
               margin-left: 6px; padding-left: 14px; }
      .tp-tl-item { margin-bottom: 12px; }
      .tp-tl-time { color: #888; font-size: 0.78rem; }
      /* --- agent pipeline banner --- */
      .tp-pipe { display: flex; align-items: stretch; margin: 4px 0 8px 0; }
      .tp-agent {
        flex: 1; border: 1px solid rgba(128,128,128,0.35); border-radius: 10px;
        padding: 10px 14px; background: rgba(128,128,128,0.07);
        opacity: 0.5; transition: opacity .3s, border-color .3s;
      }
      .tp-agent .name { font-weight: 700; font-size: 0.95rem; }
      .tp-agent .desc { color: #999; font-size: 0.74rem; }
      .tp-agent .note { margin-top: 6px; font-size: 0.8rem; color: #ccc;
                        min-height: 1.2em; }
      .tp-agent.active {
        opacity: 1; border-color: #ff4b4b; background: rgba(255,75,75,0.10);
        animation: tp-pulse 1.2s ease-in-out infinite;
      }
      @keyframes tp-pulse {
        0%, 100% { box-shadow: 0 0 5px rgba(255,75,75,0.25); }
        50%      { box-shadow: 0 0 16px rgba(255,75,75,0.55); }
      }
      .tp-arrow { align-self: center; padding: 0 12px; font-size: 1.5rem;
                  color: #777; }
      .tp-flight {
        margin-top: 6px; color: #bbb; font-size: 0.82rem; font-style: italic;
        white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
      }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_resource
def _build_client(api_key: str) -> OpenAI:
    """Cached per-key OpenAI client so swapping keys in Dev tools rebuilds it."""
    return OpenAI(api_key=api_key)


def _current_api_key() -> tuple[str, str]:
    """Return (key, source). Source is 'session', 'env', or '' if none."""
    override = st.session_state.get("openai_api_key_override", "").strip()
    if override:
        return override, "session"
    env_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if env_key:
        return env_key, "env"
    return "", ""


def get_client() -> OpenAI | None:
    """Build the OpenAI client. Returns None if no key is configured."""
    key, _ = _current_api_key()
    if not key:
        return None
    return _build_client(key)


@st.cache_data
def sources_df() -> pd.DataFrame:
    return store.read_sources()


# --- sport inference for display badges (general, no hard-coded names) ------
# Generic sport vocabulary only — no club, team, league or player name appears
# here. This is a cosmetic sidebar/feed badge; the pipeline derives sport per
# signal from the LLM (see below), never from these words.
_FOOTBALL_TERMS = {
    "midfielder", "striker", "defender", "winger", "medical",
    "release clause", "here we go", "club-to-club", "loan", "£",
}
_BASKETBALL_TERMS = {
    "nba", "free agent", "free agency", "summer league", "roster",
    "mvp", "draft", "rookie", "conference", "podcast",
}


def _keyword_sport(text: str) -> str:
    fb = sum(term in text for term in _FOOTBALL_TERMS)
    nba = sum(term in text for term in _BASKETBALL_TERMS)
    if fb > nba:
        return "Football"
    if nba > fb:
        return "Basketball"
    return "Mixed"


def source_sport_hint(handle: str) -> str:
    """Sport label for a source's badge.

    Prefers the LLM's own verdict: if this source's posts have already produced
    scored signals, use the sport the model assigned them. Before any events
    exist, fall back to a generic keyword heuristic (no proper nouns). Purely a
    display convenience — the pipeline never keys off this.
    """
    # 1. LLM-derived: map this source's raw posts -> events -> sport.
    raw = store.read_raw_posts()
    events = store.read_events()
    if not raw.empty and not events.empty:
        ids = raw.loc[raw["source_handle"] == handle, "id"].astype(str).tolist()
        ev = events[events["raw_post_id"].astype(str).isin(ids)]
        sports = [s for s in ev["sport"].tolist() if s]
        if sports:
            fb = sports.count("Football")
            nba = sports.count("Basketball")
            if fb and not nba:
                return "Football"
            if nba and not fb:
                return "Basketball"
            return "Mixed"

    # 2. Fallback before any signals: generic keyword lean over the fixture.
    fixture = store.read_fixture_posts()
    posts = fixture[fixture["source_handle"] == handle]
    if posts.empty:
        return "Mixed"
    return _keyword_sport(" ".join(posts["raw_content"].astype(str)).lower())


def sport_badge(sport: str) -> str:
    s = (sport or "").lower()
    if s.startswith("foot"):
        return '<span class="tp-badge football">⚽ Football</span>'
    if s.startswith("basket"):
        return '<span class="tp-badge basketball">🏀 NBA</span>'
    return '<span class="tp-badge mixed">• Mixed</span>'


def impact_color(score) -> str:
    """Green→amber→red gradient by impact score (0-100)."""
    try:
        s = float(score)
    except (TypeError, ValueError):
        return "#444"
    if s >= 80:
        return "#c0392b"
    if s >= 70:
        return "#e67e22"
    if s >= 45:
        return "#b7950b"
    if s >= 20:
        return "#5d8a3a"
    return "#4a6572"


def role_badge(role: str) -> str:
    """Small chip distinguishing a manager market from a player market."""
    if (role or "").lower().startswith("manag"):
        return '<span class="tp-badge role-mgr">👔 Manager</span>'
    return '<span class="tp-badge role-player">👟 Player</span>'


def role_label(role: str) -> str:
    return "👔 Manager" if (role or "").lower().startswith("manag") else "👟 Player"


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------
def init_state() -> None:
    ss = st.session_state
    ss.setdefault("running", False)
    ss.setdefault("alerts", [])      # list of dicts, newest first
    ss.setdefault("status", "Idle. Select sources and press Start.")
    ss.setdefault("flash", "")       # transient alert flash for status line
    ss.setdefault("error", "")
    ss.setdefault("selected_handles", None)
    # When the user loads their own posts, the collector must NOT top the queue
    # up from the default fixture — the loaded set is the whole queue.
    ss.setdefault("custom_posts", False)
    # Optional session-only OpenAI key override (set via sidebar → Dev tools).
    ss.setdefault("openai_api_key_override", "")
    # Pipeline banner: which agent card is lit, what each last did, and the
    # item currently in flight (post snippet or player being scored).
    ss.setdefault("stage", "")               # "collect" | "review" | "score"
    ss.setdefault("agent_notes", {"collect": "", "review": "", "score": ""})
    ss.setdefault("in_flight", "")
    # Demo pacing / collection switch (sidebar → Dev tools).
    ss.setdefault("tick_sleep", config.TICK_SLEEP_SECONDS)
    ss.setdefault("collect_enabled", True)


init_state()


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
def render_sidebar() -> list[str]:
    src = sources_df()
    src = src[src["enabled"].astype(str).isin(["1", "1.0", "True", "true"])]

    if config.LOGO_PATH.exists():
        st.sidebar.image(str(config.LOGO_PATH), width="stretch")

    st.sidebar.header("📡 Sources to monitor")
    st.sidebar.caption("Deselecting a source stops its posts entering the queue.")

    options = list(src["handle"])

    def _fmt(handle: str) -> str:
        row = src[src["handle"] == handle].iloc[0]
        tier = int(row["credibility_tier"])
        sport = source_sport_hint(handle)
        icon = {"Football": "⚽", "Basketball": "🏀"}.get(sport, "•")
        return f"{icon} @{handle}  ·  tier {tier}"

    default = st.session_state.selected_handles
    if default is None:
        default = options

    selected = st.sidebar.multiselect(
        "Accounts",
        options=options,
        default=default,
        format_func=_fmt,
        label_visibility="collapsed",
    )
    st.session_state.selected_handles = selected

    # Tier legend so the demo can point at credibility.
    with st.sidebar.expander("Credibility tiers", expanded=False):
        for _, r in src.sort_values("credibility_tier").iterrows():
            st.markdown(
                f"**@{r['handle']}** — tier {int(r['credibility_tier'])}  \n"
                f"<span style='color:#999;font-size:0.8rem'>{r['credibility_note']}</span>",
                unsafe_allow_html=True,
            )

    st.sidebar.divider()

    running = st.session_state.get("running", False)
    if running:
        st.sidebar.markdown(
            "<div style='padding:6px 10px;border-radius:8px;"
            "background:rgba(46,204,113,0.15);border:1px solid #2ecc71;"
            "color:#2ecc71;font-weight:700;text-align:center;'>"
            "🟢 Running</div>",
            unsafe_allow_html=True,
        )
    else:
        st.sidebar.markdown(
            "<div style='padding:6px 10px;border-radius:8px;"
            "background:rgba(241,196,15,0.15);border:1px solid #f1c40f;"
            "color:#f1c40f;font-weight:700;text-align:center;'>"
            "⏸ Paused</div>",
            unsafe_allow_html=True,
        )

    c1, c2 = st.sidebar.columns(2)
    if c1.button(
        "▶ Start",
        width="stretch",
        type="primary" if not running else "secondary",
        disabled=running,
    ):
        st.session_state.running = True
        st.session_state.error = ""
    if c2.button(
        "⏸ Pause",
        width="stretch",
        type="primary" if running else "secondary",
        disabled=not running,
    ):
        st.session_state.running = False
        st.session_state.status = "⏸ Paused."

    if st.sidebar.button("♻ Reset demo", width="stretch"):
        store.reset_all()
        st.session_state.running = False
        st.session_state.custom_posts = False  # back to the default fixture flow
        st.session_state.alerts = []
        st.session_state.status = "Reset. Queue and board cleared."
        st.session_state.flash = ""
        st.session_state.error = ""
        _reset_pipeline_banner()
        st.rerun()

    with st.sidebar.expander("🛠 Dev tools", expanded=False):
        st.slider(
            "Agent pace (seconds per step)",
            min_value=0.0, max_value=10.0, step=0.5,
            key="tick_sleep",
            help="Pause between pipeline steps so the audience can follow.",
        )
        st.toggle(
            "Agent 1 — collect new posts",
            key="collect_enabled",
            help="Off: no more posts enter the queue; Agents 2 and 3 "
            "keep draining what is already there.",
        )
        st.divider()

        _, key_source = _current_api_key()
        if key_source == "env":
            st.caption("🔑 OpenAI key: loaded from environment (.env).")
        elif key_source == "session":
            st.caption("🔑 OpenAI key: set for this session.")
        else:
            st.caption("🔑 OpenAI key: **not set** — paste one below to test.")

        key_input = st.text_input(
            "OpenAI API key",
            value="",
            type="password",
            placeholder="sk-...",
            help=(
                "Paste your own OpenAI key to test the app without a .env file. "
                "Stored in this browser session only — never written to disk."
            ),
            key="_openai_api_key_input",
        )
        kc1, kc2 = st.columns(2)
        if kc1.button("Save key", width="stretch"):
            if key_input.strip():
                st.session_state.openai_api_key_override = key_input.strip()
                st.session_state.status = "OpenAI key set for this session."
                st.session_state.error = ""
                st.rerun()
            else:
                st.session_state.error = "Paste a key before saving."
        if kc2.button("Clear key", width="stretch"):
            st.session_state.openai_api_key_override = ""
            st.session_state.status = "Session key cleared."
            st.rerun()

        st.divider()
        st.caption(
            "Reprocess: same posts, board cleared, re-run Agents 2+3 "
            "without re-fetching."
        )
        if st.button("Reprocess", width="stretch"):
            store.reprocess_all()
            st.session_state.running = False
            st.session_state.alerts = []
            st.session_state.status = "Reprocess ready. Press Start to re-score."
            st.session_state.flash = ""
            st.session_state.error = ""
            _reset_pipeline_banner()
            st.rerun()

        st.divider()
        st.caption(
            "Load your own posts: a CSV with the raw_posts columns "
            "(id · source_handle · post_url · author · posted_at · "
            "raw_content). Replaces the current queue and clears the board."
        )
        upload = st.file_uploader(
            "Upload raw_posts CSV", type="csv", label_visibility="collapsed"
        )
        if upload is not None and st.button("Load posts", width="stretch"):
            try:
                df = pd.read_csv(upload, dtype=str, keep_default_na=False)
            except Exception as exc:
                st.session_state.error = f"Could not read CSV: {exc}"
                st.rerun()
            required = {"source_handle", "post_url", "raw_content"}
            missing = required - set(df.columns)
            if missing:
                st.session_state.error = (
                    f"CSV missing required column(s): {', '.join(sorted(missing))}"
                )
            elif df.empty:
                st.session_state.error = "That CSV has no rows."
            else:
                store.load_raw_posts(df)
                st.session_state.custom_posts = True
                st.session_state.running = False
                st.session_state.alerts = []
                st.session_state.status = (
                    f"Loaded {len(df)} post(s). Press Start to process."
                )
                st.session_state.flash = ""
                st.session_state.error = ""
                _reset_pipeline_banner()
            st.rerun()

    _render_manual_post_form(selected)

    return selected


def _render_manual_post_form(selected: list[str]) -> None:
    """Inject a fictional post into the queue — only the text is required.

    Every other field (author, handle, date, url) is pre-filled so a demo can
    drop in a brand-new rumour and watch it flow through Agents 1→2→3. The post
    lands with ``processed = 0``, so it is picked up on the next tick exactly
    like a fetched post.
    """
    # Pulse the expander header ("Add a fictional post") to invite the user to
    # open it. The anchor span sits in the element-container immediately before
    # the expander, so :has() + adjacent-sibling reaches exactly that header —
    # the other sidebar expanders (Dev tools, Credibility tiers) stay still.
    st.sidebar.markdown(
        """
        <style>
          @keyframes tp-fic-pulse {
            0%, 100% { box-shadow: 0 0 3px rgba(255,75,75,0.25); }
            50%      { box-shadow: 0 0 16px rgba(255,75,75,0.80); }
          }
          section[data-testid="stSidebar"]
            [data-testid="stElementContainer"]:has(.tp-fic-anchor)
            + [data-testid="stElementContainer"] [data-testid="stExpander"] summary {
            animation: tp-fic-pulse 1.6s ease-in-out infinite;
            border-radius: 8px;
          }
        </style>
        <span class="tp-fic-anchor"></span>
        """,
        unsafe_allow_html=True,
    )
    with st.sidebar.expander("➕ Add a fictional post", expanded=False):
        st.caption("Type a rumour and watch it flow through Agents 1 → 2 → 3.")
        with st.form("manual_post", clear_on_submit=True):
            text = st.text_area(
                "Post text",
                placeholder="e.g. Fabrizio Romano: Julian Alvarez to Chelsea, "
                "here we go! Deal agreed, medical booked.",
                height=120,
            )
            submitted = st.form_submit_button(
                "Inject post", width="stretch", type="primary"
            )

        if submitted:
            if not text.strip():
                st.warning("Add some post text first.")
            else:
                # Source/author/date are demo boilerplate — a selected handle
                # (so the credibility tier resolves) and "now" are plenty.
                handle = selected[0] if selected else "FabrizioRomano"
                new_id = store.next_id("raw_posts")
                store.append_raw_posts(
                    [
                        {
                            "id": str(new_id),
                            "source_handle": handle,
                            # Unique URL so the collector never dedups it away.
                            "post_url": f"manual://post/{new_id}/{int(time.time())}",
                            "author": "Demo Insider",
                            "posted_at": _now_iso(),
                            "raw_content": text.strip(),
                            "fetched_at": _now_iso(),
                            "processed": "0",
                        }
                    ]
                )
                st.session_state.status = (
                    f"➕ Injected fictional post from @{handle}. "
                    "Press Start to process."
                )
                st.rerun()


# ---------------------------------------------------------------------------
# Pipeline — exactly one unit of work per call
# ---------------------------------------------------------------------------
def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _source_credibility(handle: str) -> tuple[int, str]:
    src = sources_df()
    row = src[src["handle"] == handle]
    if row.empty:
        return config.DEFAULT_CREDIBILITY_TIER, "Unknown source; default tier."
    r = row.iloc[0]
    return int(r["credibility_tier"]), str(r["credibility_note"])


def run_one_tick(selected: list[str], client: OpenAI) -> bool:
    """Do one unit of pipeline work. Returns True if work was done.

    Priority:
      1. Queue has < 3 unprocessed posts and fixture not exhausted → Agent 1.
      2. An events row is unpriced → Agent 3 on the oldest.
      3. A raw post is unprocessed → Agent 2 on the oldest.
    """
    raw = store.read_raw_posts()
    unprocessed = (
        raw[raw["processed"].astype(str) == "0"] if not raw.empty
        else raw
    )

    # --- 1. keep the queue topped up ---------------------------------------
    # Skip the fixture entirely when the user has loaded their own posts: the
    # loaded set IS the whole queue, so the collector must not add defaults.
    # The Dev tools toggle can also switch collection off mid-demo.
    if (
        st.session_state.get("collect_enabled", True)
        and not st.session_state.get("custom_posts", False)
        and len(unprocessed) < config.FETCH_BATCH_SIZE
        and collector.unreleased_count(selected) > 0
    ):
        rows = collector.fetch_new_posts(selected, config.FETCH_BATCH_SIZE)
        if rows:
            store.append_raw_posts(rows)
            handles = ", ".join(f"@{r['source_handle']}" for r in rows)
            note = f"Fetched {len(rows)} post(s) — {handles}"
            _set_stage("collect", note, _snippet(rows[0]["raw_content"]))
            st.session_state.status = f"Agent 1 · Content Collector: {note}"
            return True

    # --- 2. price the oldest unpriced signal -------------------------------
    events = store.read_events()
    if not events.empty:
        unpriced = events[events["impact_score"].astype(str).str.strip() == ""]
        if not unpriced.empty:
            row = unpriced.sort_values("id", key=lambda c: pd.to_numeric(c)).iloc[0]
            event = row.to_dict()
            player = event.get("player", "")
            _set_stage("score", f"Scoring {player}…", _snippet(event.get("headline", "")))
            st.session_state.status = f"Agent 3 · Trading Analyst: scoring {player}…"

            # Build this market's already-priced timeline, oldest first.
            same = events[events["story_key"] == event["story_key"]]
            priced = same[same["impact_score"].astype(str).str.strip() != ""]
            priced = priced.sort_values(
                "event_date"
            ) if not priced.empty else priced
            timeline = priced.to_dict("records")

            # events.csv has no source_handle column — look it up via the
            # originating raw post so Agent 3 gets the right credibility tier.
            handle = _handle_for_event(raw, event)
            tier, note = _source_credibility(handle)

            assessment, alert = score_event(event, timeline, tier, note, client)

            # Persist the impact columns onto this row (app.py owns writes).
            events = store.read_events()
            mask = events["id"] == event["id"]
            events.loc[mask, "is_new_information"] = str(assessment.is_new_information)
            events.loc[mask, "impact_score"] = str(assessment.impact_score)
            events.loc[mask, "impact_rationale"] = assessment.impact_rationale
            events.loc[mask, "suggested_action"] = assessment.suggested_action
            events.loc[mask, "alert"] = str(alert)
            store.write_events(events)

            # Mark the source post processed if not already.
            _mark_processed(event.get("raw_post_id", ""))

            bell = " 🔔" if alert else ""
            st.session_state.agent_notes["score"] = (
                f"{player}: impact {assessment.impact_score}{bell}"
            )

            if alert:
                _push_alert(event, assessment.impact_score, assessment.impact_rationale)
                st.session_state.flash = (
                    f"🔔 ALERT · {event.get('player','')} ({assessment.impact_score})"
                )
                st.session_state.status = st.session_state.flash
                # Fan the alert out to Slack. "No changes needed" never fires an
                # alert anyway, but guard explicitly so it is never sent.
                if assessment.suggested_action != "No changes needed":
                    ok, detail = notifier.send_alert(
                        market=_market_title(
                            event.get("player", ""), event.get("sport", "")
                        ),
                        summary=event.get("summary_llm", ""),
                        raw_post=event.get("raw_content", ""),
                        suggested_action=assessment.suggested_action,
                    )
                    if ok:
                        st.toast("✅ Slack alert sent", icon="💬")
                    else:
                        # Toast + error banner: banner may be cleared on the
                        # next successful tick, but the toast persists briefly.
                        st.toast(f"⚠ Slack: {detail}", icon="⚠")
                        st.session_state.error = f"Slack alert failed: {detail}"
                        print(f"[notifier] Slack alert failed: {detail}", flush=True)
            return True

    # --- 3. assess the oldest unprocessed post -----------------------------
    if not unprocessed.empty:
        row = unprocessed.sort_values("id", key=lambda c: pd.to_numeric(c)).iloc[0]
        post = row.to_dict()
        handle = post.get("source_handle", "")
        _set_stage("review", f"Reading @{handle}…", _snippet(post.get("raw_content", "")))
        st.session_state.status = f"Agent 2 · Content Reviewer: reading @{handle}…"

        # Give Agent 2 the markets already open so it reuses an existing
        # player spelling instead of splitting one person into two buckets.
        assessment = assess_post(post, client, _open_market_players())

        # Append one events row per detected signal (impact columns empty).
        for sig in assessment.signals:
            clubs_str = "; ".join(sig.clubs)
            store.append_event(
                {
                    "id": str(store.next_id("events")),
                    "raw_post_id": post["id"],
                    "event_date": post.get("posted_at", ""),
                    "sport": sig.sport,
                    "headline": sig.headline,
                    "raw_content": post.get("raw_content", ""),
                    "summary_llm": sig.summary,
                    "player": sig.player,
                    "role": sig.role,
                    "story_key": store.normalise(sig.player),
                    "clubs": clubs_str,
                    "is_new_information": "",
                    "impact_score": "",
                    "impact_rationale": "",
                    "suggested_action": "",
                    "alert": "",
                }
            )

        # If the post produced no signals, it is done now; otherwise Agent 3
        # marks it processed when it prices the last of its signals. To keep
        # bookkeeping simple and idempotent, mark processed here regardless —
        # the events rows are already written, so reprocessing is driven by
        # reprocess_all(), never by the processed flag alone.
        _mark_processed(post["id"])
        n = len(assessment.signals)
        st.session_state.agent_notes["review"] = f"@{handle} → {n} signal(s)"
        st.session_state.status = (
            f"Agent 2 · Content Reviewer: @{handle} → {n} signal(s)"
        )
        return True

    return False


def _snippet(text: str, limit: int = 140) -> str:
    text = " ".join(str(text or "").split())
    return text[: limit - 1] + "…" if len(text) > limit else text


def _set_stage(stage: str, note: str, in_flight: str) -> None:
    """Light up one agent card and record what it is doing."""
    st.session_state.stage = stage
    st.session_state.agent_notes[stage] = note
    st.session_state.in_flight = in_flight


def _reset_pipeline_banner() -> None:
    st.session_state.stage = ""
    st.session_state.agent_notes = {"collect": "", "review": "", "score": ""}
    st.session_state.in_flight = ""


def _handle_for_event(raw: pd.DataFrame, event: dict) -> str:
    """Look up the source handle for an event via its raw_post_id."""
    if raw.empty:
        return ""
    match = raw[raw["id"].astype(str) == str(event.get("raw_post_id", ""))]
    if match.empty:
        return ""
    return str(match.iloc[0]["source_handle"])


def _mark_processed(raw_post_id: str) -> None:
    if raw_post_id == "" or raw_post_id is None:
        return
    raw = store.read_raw_posts()
    if raw.empty:
        return
    mask = raw["id"].astype(str) == str(raw_post_id)
    if mask.any():
        raw.loc[mask, "processed"] = "1"
        store.write_raw_posts(raw)


def _push_alert(event: dict, score, rationale: str) -> None:
    st.session_state.alerts.insert(
        0,
        {
            "time": _now_iso(),
            "sport": event.get("sport", ""),
            "player": event.get("player", ""),
            "market": _market_title(event.get("player", ""), event.get("sport", "")),
            "score": score,
            "rationale": rationale,
        },
    )


def _market_title(player: str, sport: str) -> str:
    suffix = "next team" if (sport or "").lower().startswith("basket") else "next club"
    return f"{player} {suffix}"


def _open_market_players() -> list[str]:
    """One canonical player name per open market, for Agent 2 dedup.

    Markets are keyed by ``story_key`` (normalised name). For each key we hand
    the model the longest spelling seen so far — the fullest name is the safest
    canonical form to reuse, so a later "Alvarez" collapses onto an existing
    "Julian Alvarez" instead of opening a second bucket.
    """
    events = store.read_events()
    if events.empty:
        return []
    names: list[str] = []
    for _, grp in events.groupby("story_key"):
        candidates = [str(p).strip() for p in grp["player"] if str(p).strip()]
        if candidates:
            names.append(max(candidates, key=len))
    return sorted(names)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
# (stage_key, card_title, short_subtitle, long_tooltip)
_PIPELINE_AGENTS = [
    (
        "collect",
        "📥 Agent 1 · Content Collector",
        "Fetches posts via X.com API",
        "Not an LLM. Collects posts from social media via the X.com API — "
        "monitors selected journalists, trusted insiders and sports news "
        "accounts, then hands the batch to Agent 2 for analysis.",
    ),
    (
        "review",
        "🔎 Agent 2 · Content Reviewer",
        "Filters transfer-related posts",
        "LLM-based, powered by OpenAI GPT-4o. Reviews each post to judge "
        "its topic and relevance, and filters out anything unrelated to "
        "player or manager transfers. Keeps rumours, negotiations and "
        "official confirmations, then sends the filtered content to Agent 3.",
    ),
    (
        "score",
        "📊 Agent 3 · Trading Analyst",
        "Evaluates signals & fires alerts",
        "LLM-based, powered by OpenAI GPT-4o. Organises transfer events "
        "into a timeline for each player or manager, compares new posts "
        "with previous ones to see how the story is evolving, and "
        "recommends the most appropriate action for the trading team. "
        "Significant new events automatically fire a Slack alert with "
        "the player/manager, a summary and the recommended trading action.",
    ),
]


def render_pipeline() -> None:
    """The demo centrepiece: three agent cards, the active one lit up.

    One unit of work runs per Streamlit rerun, so the highlight hops from
    card to card as a post travels through the pipeline — no JS needed.
    """
    active = st.session_state.stage if st.session_state.running else ""
    notes = st.session_state.agent_notes
    cards = []
    for stage, name, desc, tooltip in _PIPELINE_AGENTS:
        cls = "tp-agent active" if stage == active else "tp-agent"
        note = notes.get(stage, "")
        # `title=` gives a native browser tooltip on hover with the fuller
        # description — no extra UI, works everywhere.
        safe_tip = tooltip.replace('"', "&quot;")
        cards.append(
            f"<div class='{cls}' title=\"{safe_tip}\">"
            f"<div class='name'>{name}</div>"
            f"<div class='desc'>{desc}</div>"
            f"<div class='note'>{note}</div></div>"
        )
    arrow = "<div class='tp-arrow'>➜</div>"
    html = f"<div class='tp-pipe'>{arrow.join(cards)}</div>"
    if st.session_state.running and st.session_state.in_flight:
        html += f"<div class='tp-flight'>“{st.session_state.in_flight}”</div>"
    st.markdown(html, unsafe_allow_html=True)


def render_alert_strip() -> None:
    st.subheader("🔔 Trading Alerts")
    alerts = st.session_state.alerts
    if not alerts:
        st.caption("No alerts yet.")
        return
    for a in alerts[:8]:
        t = a["time"][11:16] if len(a["time"]) >= 16 else a["time"]
        badge = "🏀 NBA" if (a["sport"] or "").lower().startswith("basket") else "⚽ Football"
        st.markdown(
            f"<div class='tp-alert'>🔔 <b>{t}</b> · {badge} · "
            f"<span class='market'>{a['market']}</span> "
            f"<span style='color:#ff4b4b;font-weight:700'>({a['score']})</span> — "
            f"{a['rationale']}</div>",
            unsafe_allow_html=True,
        )


def render_kpis() -> None:
    raw = store.read_raw_posts()
    events = store.read_events()
    posts_scanned = int((raw["processed"].astype(str) == "1").sum()) if not raw.empty else 0
    signals = len(events)
    markets = events["story_key"].nunique() if not events.empty else 0
    alerts_fired = (
        int((events["alert"].astype(str).isin(["True", "true"])).sum())
        if not events.empty else 0
    )
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Posts scanned", posts_scanned)
    c2.metric("Signals detected", signals)
    c3.metric("Markets open", markets)
    c4.metric("Alerts fired", alerts_fired)


def render_live_feed() -> None:
    raw = store.read_raw_posts()
    if raw.empty:
        st.info("Queue is empty. Press Start to fetch posts.")
        return
    df = raw.sort_values("id", key=lambda c: pd.to_numeric(c), ascending=False)
    rows = []
    for _, r in df.iterrows():
        processed = str(r["processed"]) == "1"
        sport = source_sport_hint(r["source_handle"])
        badge = "🏀 NBA" if sport == "Basketball" else ("⚽ Football" if sport == "Football" else "• Mixed")
        rows.append(
            {
                "": "✅" if processed else "⏳",
                "Sport": badge,
                "Time": str(r["posted_at"])[:16].replace("T", " "),
                "Source": f"@{r['source_handle']}",
                "Post": r["raw_content"],
            }
        )
    st.dataframe(
        pd.DataFrame(rows),
        width="stretch",
        hide_index=True,
        column_config={
            "": st.column_config.TextColumn(width="small"),
            "Post": st.column_config.TextColumn(width="large"),
        },
    )


def _compose_impact(row) -> str:
    score = str(row.get("impact_score", "") or "").strip()
    if score == "":
        return "⏳ evaluating…"
    bell = " 🔔" if str(row.get("alert", "")) in ("True", "true") else ""
    return f"{score}{bell} — {row.get('impact_rationale','')}"


def render_signals() -> None:
    events = store.read_events()
    if events.empty:
        st.info("No signals yet. They appear as posts are assessed.")
        return

    # Filters.
    fc1, fc2, fc3 = st.columns([1, 2, 1])
    sports = ["All"] + sorted(events["sport"].replace("", pd.NA).dropna().unique().tolist())
    sport_f = fc1.selectbox("Sport", sports)
    markets = ["All"] + sorted(
        (events["player"].map(lambda p: _market_title(p, _sport_for_player(events, p)))).unique().tolist()
    )
    market_f = fc2.selectbox("Market", markets)
    alerts_only = fc3.checkbox("Alerts only")

    view = events.copy()
    if sport_f != "All":
        view = view[view["sport"] == sport_f]
    if alerts_only:
        view = view[view["alert"].astype(str).isin(["True", "true"])]

    view = view.sort_values("id", key=lambda c: pd.to_numeric(c), ascending=False)

    table = []
    for _, r in view.iterrows():
        market = _market_title(r["player"], r["sport"])
        if market_f != "All" and market != market_f:
            continue
        table.append(
            {
                "DATE": str(r["event_date"])[:16].replace("T", " "),
                "HEADLINE": r["headline"],
                "RAW_CONTENT": r["raw_content"],
                "SUMMARY_LLM": r["summary_llm"],
                "CLUBS": r["clubs"],
                "IMPACT_LLM": _compose_impact(r),
                "MARKET": market,
                "_score": r["impact_score"],
                "_alert": str(r["alert"]) in ("True", "true"),
            }
        )

    if not table:
        st.caption("No signals match the filters.")
        return

    tdf = pd.DataFrame(table)

    def _style(row):
        color = impact_color(row["_score"])
        return [f"background-color: {color}22"] * len(row)

    display_cols = ["DATE", "HEADLINE", "RAW_CONTENT", "SUMMARY_LLM", "CLUBS", "IMPACT_LLM", "MARKET"]
    styler = tdf.style.apply(_style, axis=1)
    st.dataframe(
        styler,
        width="stretch",
        hide_index=True,
        column_order=display_cols,
        column_config={
            "_score": None,
            "_alert": None,
            "RAW_CONTENT": st.column_config.TextColumn(width="medium"),
            "SUMMARY_LLM": st.column_config.TextColumn(width="medium"),
            "IMPACT_LLM": st.column_config.TextColumn(width="large"),
        },
    )


def _sport_for_player(events: pd.DataFrame, player: str) -> str:
    row = events[events["player"] == player]
    if row.empty:
        return ""
    return str(row.iloc[0]["sport"])


def _market_status(group: pd.DataFrame) -> str:
    """A market's current status = the suggested_action of its latest event."""
    priced = group[group["suggested_action"].astype(str).str.strip() != ""]
    if priced.empty:
        return "Evaluating…"
    latest = priced.sort_values("event_date").iloc[-1]
    return str(latest["suggested_action"]) or "Evaluating…"


def render_markets() -> None:
    events = store.read_events()
    if events.empty:
        st.info("No markets yet. They open as the first rumours are evaluated.")
        return

    # Order markets by most recent activity.
    order = (
        events.groupby("story_key")["event_date"].max().sort_values(ascending=False)
    )

    # Map raw_post_id -> source handle so each timeline event can show who
    # said it, alongside the raw post, for fact-checking.
    raw = store.read_raw_posts()
    handle_by_post = (
        dict(zip(raw["id"].astype(str), raw["source_handle"]))
        if not raw.empty else {}
    )

    # Precompute per-market metadata (used by both the slicers and the cards).
    meta: dict[str, dict] = {}
    for key in order.index:
        g = events[events["story_key"] == key].sort_values("event_date")
        meta[key] = {
            "player": g.iloc[-1]["player"],
            "sport": g.iloc[-1]["sport"],
            "role": g.iloc[-1].get("role", "") or "Player",
            "status": _market_status(g),
        }

    # --- slicers: Player · Sport · Status ---------------------------------
    fc1, fc2, fc3 = st.columns(3)
    players = ["All"] + sorted({m["player"] for m in meta.values() if m["player"]})
    player_f = fc1.selectbox("Player / Manager", players, key="mk_player")
    sports = ["All"] + sorted({m["sport"] for m in meta.values() if m["sport"]})
    sport_f = fc2.selectbox("Sport", sports, key="mk_sport")
    statuses = ["All"] + sorted({m["status"] for m in meta.values() if m["status"]})
    status_f = fc3.selectbox("Status", statuses, key="mk_status")

    shown = 0
    for key in order.index:
        m = meta[key]
        if player_f != "All" and m["player"] != player_f:
            continue
        if sport_f != "All" and m["sport"] != sport_f:
            continue
        if status_f != "All" and m["status"] != status_f:
            continue
        shown += 1

        group = events[events["story_key"] == key].copy().sort_values("event_date")
        player, sport, role = m["player"], m["sport"], m["role"]
        title = _market_title(player, sport)

        # Peak impact across the timeline.
        scores = pd.to_numeric(group["impact_score"], errors="coerce").dropna()
        peak = int(scores.max()) if not scores.empty else None
        any_alert = group["alert"].astype(str).isin(["True", "true"]).any()

        # Clubs seen across the whole timeline, deduped, order preserved.
        seen: list[str] = []
        for c in group["clubs"]:
            for club in str(c).split(";"):
                club = club.strip()
                if club and club not in seen:
                    seen.append(club)

        peak_txt = f" · peak {peak}" if peak is not None else ""
        bell = " 🔔" if any_alert else ""
        sport_icon = "🏀" if sport.lower().startswith("basket") else "⚽"
        mgr_icon = " 👔" if str(role).lower().startswith("manag") else ""
        label = f"{sport_icon}{mgr_icon}  {title} · {m['status']}{peak_txt}{bell}"

        with st.expander(label, expanded=False):
            st.markdown(
                sport_badge(sport) + " " + role_badge(role),
                unsafe_allow_html=True,
            )
            if seen:
                linked = "Teams linked so far" if str(role).lower().startswith("manag") else "Clubs linked so far"
                st.markdown(
                    f"<span class='tp-clublist'>{linked}: "
                    f"{', '.join(seen)}</span>",
                    unsafe_allow_html=True,
                )
            st.markdown("<div class='tp-tl'>", unsafe_allow_html=True)
            for _, ev in group.iterrows():
                t = str(ev["event_date"])[:16].replace("T", " ")
                score = str(ev["impact_score"] or "").strip()
                if score == "":
                    chip = "<span class='tp-chip' style='background:#666'>evaluating…</span>"
                else:
                    chip = (
                        f"<span class='tp-chip' style='background:{impact_color(score)}'>"
                        f"{score}</span>"
                    )
                ev_bell = " 🔔" if str(ev["alert"]) in ("True", "true") else ""
                action = ev["suggested_action"] or ""
                action_txt = f" · <i>{action}</i>" if action else ""
                raw_txt = str(ev.get("raw_content", "") or "")
                src = handle_by_post.get(str(ev.get("raw_post_id", "")), "")
                src_txt = f"@{src}: " if src else ""
                st.markdown(
                    f"<div class='tp-tl-item'>"
                    f"<span class='tp-tl-time'>{t}</span> {chip}{ev_bell}{action_txt}<br>"
                    f"<b>{ev['headline']}</b><br>"
                    f"<span style='color:#bbb'>{ev['summary_llm']}</span>"
                    f"<div class='tp-raw'>“{src_txt}{raw_txt}”</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
            st.markdown("</div>", unsafe_allow_html=True)

    if shown == 0:
        st.caption("No markets match the filters.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    st.title("📡 TransferPulse AI")
    st.caption(
        "Real-time transfer & free-agency intelligence — spot and evaluate "
        "Next Club / Next Team markets before the field."
    )

    selected = render_sidebar()
    client = get_client()

    if client is None:
        st.error(
            "No `OPENAI_API_KEY` found. Either add it to a `.env` file next to "
            "`app.py` (see `.env.example`) **or** paste one into the sidebar → "
            "🛠 Dev tools → OpenAI API key."
        )
        st.session_state.running = False

    # Status line.
    status_box = st.container()

    render_alert_strip()
    st.divider()
    render_kpis()
    st.divider()

    tab_feed, tab_signals, tab_markets = st.tabs(
        ["📥 Live Feed", "🎯 Signals", "🗂 Timeline Board"]
    )
    with tab_feed:
        render_live_feed()
    with tab_signals:
        render_signals()
    with tab_markets:
        render_markets()

    # --- pipeline banner + status line --------------------------------------
    with status_box:
        if st.session_state.error:
            st.error(st.session_state.error)
        render_pipeline()
        run_flag = "🟢 Running" if st.session_state.running else "⚪ Idle"
        st.markdown(
            f"<div class='tp-status'>{run_flag} &nbsp; | &nbsp; "
            f"{st.session_state.status}</div>",
            unsafe_allow_html=True,
        )

    # --- the loop: one unit of work, then rerun ----------------------------
    if st.session_state.running and client is not None:
        if not selected:
            st.session_state.status = "No sources selected — nothing to fetch."
            st.session_state.running = False
            return
        pace = float(st.session_state.tick_sleep)
        try:
            did_work = run_one_tick(selected, client)
        except Exception as exc:  # surface, leave work undone to retry
            st.session_state.error = f"Pipeline error (will retry): {exc}"
            time.sleep(pace)
            st.rerun()
            return

        if did_work:
            st.session_state.error = ""
            time.sleep(pace)
            st.rerun()
        else:
            # Queue empty and everything priced.
            st.session_state.running = False
            st.session_state.status = "✅ Done — queue drained, all signals evaluated."
            st.rerun()


if __name__ == "__main__":
    main()
