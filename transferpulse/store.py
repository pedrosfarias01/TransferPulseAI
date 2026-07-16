"""Thin CSV persistence layer for TransferPulse AI.

Every read and write of a CSV happens here so the rest of the app never has to
think about encodings, headers or file creation. The "database" is two CSVs in
data/ read through pandas; the fixtures in fixtures/ and config/ are read-only.

Single-threaded by design: everything runs inside Streamlit's rerun loop, never
a background thread.
"""

from __future__ import annotations

import time
import unicodedata

import pandas as pd

import config

# The data/ CSVs may live under OneDrive (or briefly get locked by antivirus or
# an open Excel window). Those locks clear in milliseconds, so a tiny retry
# makes reads/writes robust without any threading.
_RETRIES = 5
_RETRY_SLEEP = 0.15


def _with_retry(fn):
    last = None
    for attempt in range(_RETRIES):
        try:
            return fn()
        except (PermissionError, OSError) as exc:
            last = exc
            time.sleep(_RETRY_SLEEP * (attempt + 1))
    raise last

# Canonical column order for the two live tables. Creating a fresh file always
# uses these so headers stay stable across resets.
RAW_POSTS_COLUMNS = [
    "id", "source_handle", "post_url", "author", "posted_at",
    "raw_content", "fetched_at", "processed",
]

EVENTS_COLUMNS = [
    "id", "raw_post_id", "event_date", "sport", "headline", "raw_content",
    "summary_llm", "player", "role", "story_key", "clubs",
    "is_new_information", "impact_score", "impact_rationale",
    "suggested_action", "alert",
]

_SCHEMAS = {
    config.RAW_POSTS_CSV: RAW_POSTS_COLUMNS,
    config.EVENTS_CSV: EVENTS_COLUMNS,
}


# --- text helpers ----------------------------------------------------------
def normalise(text: str) -> str:
    """Trim, lowercase and strip accents so player names group cleanly.

    An accented spelling and its plain-ASCII form must collapse to the same
    market key (e.g. "Núñez" and "Nunez"), so one player never splits into two.
    """
    if text is None:
        return ""
    text = str(text).strip().lower()
    # Decompose accented characters and drop the combining marks.
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    # Collapse internal whitespace runs to single spaces.
    return " ".join(stripped.split())


# --- low-level csv access --------------------------------------------------
def _ensure_file(path) -> None:
    """Create an empty table with the right headers if it doesn't exist."""
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        pd.DataFrame(columns=_SCHEMAS[path]).to_csv(
            path, index=False, encoding="utf-8"
        )


def _read(path, *, source: bool = False) -> pd.DataFrame:
    """Read a CSV as strings (so ids/flags survive round-trips unchanged).

    ``source=True`` reads a provided fixture, which carries a UTF-8 BOM.
    """
    encoding = "utf-8-sig" if source else "utf-8"
    if not source:
        _ensure_file(path)
    return _with_retry(
        lambda: pd.read_csv(
            path, dtype=str, keep_default_na=False, encoding=encoding
        )
    )


def _write(path, df: pd.DataFrame) -> None:
    """Write a live table back out, keeping the canonical column order."""
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    cols = _SCHEMAS.get(path)
    if cols is not None:
        # Reindex guards against a stray/missing column creeping in.
        df = df.reindex(columns=cols)
    _with_retry(lambda: df.to_csv(path, index=False, encoding="utf-8"))


# --- public read helpers ---------------------------------------------------
def read_fixture_posts() -> pd.DataFrame:
    """The provided 30-post fixture Agent 1 releases from (read-only)."""
    return _read(config.FIXTURES_POSTS, source=True)


def read_sources() -> pd.DataFrame:
    """The provided credibility-tier table (read-only)."""
    df = _read(config.SOURCES_CSV, source=True)
    # credibility_tier is used numerically downstream.
    df["credibility_tier"] = pd.to_numeric(
        df["credibility_tier"], errors="coerce"
    ).fillna(config.DEFAULT_CREDIBILITY_TIER).astype(int)
    return df


def read_raw_posts() -> pd.DataFrame:
    """The live queue."""
    return _read(config.RAW_POSTS_CSV)


def read_events() -> pd.DataFrame:
    """The signals / board."""
    return _read(config.EVENTS_CSV)


# --- public write helpers --------------------------------------------------
def write_raw_posts(df: pd.DataFrame) -> None:
    _write(config.RAW_POSTS_CSV, df)


def write_events(df: pd.DataFrame) -> None:
    _write(config.EVENTS_CSV, df)


def append_raw_posts(rows: list[dict]) -> None:
    if not rows:
        return
    df = read_raw_posts()
    df = pd.concat([df, pd.DataFrame(rows)], ignore_index=True)
    write_raw_posts(df)


def append_event(row: dict) -> None:
    df = read_events()
    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    write_events(df)


def next_id(table: str) -> int:
    """max existing id + 1 for 'raw_posts' or 'events' (1 when empty)."""
    path = config.RAW_POSTS_CSV if table == "raw_posts" else config.EVENTS_CSV
    df = _read(path)
    if df.empty or "id" not in df.columns:
        return 1
    ids = pd.to_numeric(df["id"], errors="coerce").dropna()
    return int(ids.max()) + 1 if not ids.empty else 1


# --- lifecycle -------------------------------------------------------------
def reset_all() -> None:
    """Clear the queue and the board back to headers. Never touch fixtures/."""
    _write(config.RAW_POSTS_CSV, pd.DataFrame(columns=RAW_POSTS_COLUMNS))
    _write(config.EVENTS_CSV, pd.DataFrame(columns=EVENTS_COLUMNS))


def load_raw_posts(df: pd.DataFrame) -> None:
    """Replace the live queue with user-supplied raw posts and clear the board.

    ``df`` must carry the raw_posts columns. Every row is marked unprocessed so
    the pipeline runs them from scratch; the events board is wiped so nothing
    from the previous run lingers.
    """
    df = df.copy()
    df["processed"] = "0"
    _write(config.RAW_POSTS_CSV, df)
    _write(config.EVENTS_CSV, pd.DataFrame(columns=EVENTS_COLUMNS))


def reprocess_all() -> None:
    """Re-run Agents 2+3 on the same posts without re-fetching.

    Every queued post is marked unprocessed and the board is cleared, so a
    re-run re-assesses identical posts and appends no duplicate rows.
    """
    df = read_raw_posts()
    if not df.empty:
        df["processed"] = "0"
        write_raw_posts(df)
    _write(config.EVENTS_CSV, pd.DataFrame(columns=EVENTS_COLUMNS))
