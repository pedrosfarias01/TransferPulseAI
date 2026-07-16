"""Agent 1 — Collector.

Reads the provided posts fixture and releases the next few not-yet-released
rows into the live queue, honouring the sources currently selected in the
sidebar. No live scraping: the whole read sits behind ``fetch_new_posts`` so a
real adapter could be slotted in later without touching the rest of the
pipeline.

The orchestrator (app.py) owns writes — this returns the rows to append.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

import store


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_new_posts(
    selected_handles: list[str],
    batch_size: int,
) -> list[dict]:
    """Release the next ``batch_size`` unreleased fixture posts.

    Only posts from ``selected_handles`` are eligible — deselecting a source in
    the sidebar means its posts never enter the queue. Rows already present in
    the live queue (matched by ``post_url``) are skipped, so Start/Stop/resume
    never double-inserts.

    Returns a list of raw_posts row dicts (with ``fetched_at`` stamped and
    ``processed = 0``) for the orchestrator to append.
    """
    fixture = store.read_fixture_posts()
    if fixture.empty:
        return []

    queue = store.read_raw_posts()
    already_released = set(queue["post_url"]) if not queue.empty else set()

    selected = set(selected_handles or [])

    # Fixture is chronological; keep that order. Take eligible rows that are
    # from a selected source and not yet in the queue, up to the batch size.
    to_release: list[dict] = []
    next_id = store.next_id("raw_posts")
    for _, row in fixture.iterrows():
        if len(to_release) >= batch_size:
            break
        if row["source_handle"] not in selected:
            continue
        if row["post_url"] in already_released:
            continue
        to_release.append(
            {
                "id": str(next_id),
                "source_handle": row["source_handle"],
                "post_url": row["post_url"],
                "author": row["author"],
                "posted_at": row["posted_at"],
                "raw_content": row["raw_content"],
                "fetched_at": _now_iso(),
                "processed": "0",
            }
        )
        already_released.add(row["post_url"])
        next_id += 1

    return to_release


def unreleased_count(selected_handles: list[str]) -> int:
    """How many eligible fixture posts have not yet reached the queue.

    Lets the orchestrator know when the source is exhausted so the loop can
    stop instead of spinning on Agent 1 forever.
    """
    fixture = store.read_fixture_posts()
    if fixture.empty:
        return 0
    queue = store.read_raw_posts()
    already_released = set(queue["post_url"]) if not queue.empty else set()
    selected = set(selected_handles or [])
    eligible = fixture[
        fixture["source_handle"].isin(selected)
        & ~fixture["post_url"].isin(already_released)
    ]
    return len(eligible)
