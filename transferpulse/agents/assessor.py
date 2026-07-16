"""Agent 2 — Assessment.

Reads one unprocessed post and, in a single LLM call, returns a list of the
player transfer / free-agency signals it carries — zero, one, or several. The
response shape is enforced by a Pydantic schema (structured output), never by
parsing free text.

The market is the *player*; the clubs a post names are only the runners inside
that market. Two posts about the same player naming different clubs are the
same market, never a new one.
"""

from __future__ import annotations

from typing import List, Literal

from openai import OpenAI
from pydantic import BaseModel, Field

import config


class Signal(BaseModel):
    """One transfer / free-agency signal carried by a post.

    The subject is a person — a player OR a manager/head coach — whose next
    club, team or job is in play. That person is the market.
    """

    player: str = Field(
        description="Full name of the person (player or manager) whose next "
        "club/team/job this is about. No nicknames. Spelled consistently so "
        "the same person always produces the same market."
    )
    role: Literal["Player", "Manager"] = Field(
        description="Player for an athlete; Manager for a head coach / manager "
        "(including a national-team manager)."
    )
    sport: Literal["Football", "Basketball"] = Field(
        description="Football for soccer, Basketball for the NBA."
    )
    clubs: List[str] = Field(
        default_factory=list,
        description="Every club, franchise or national team this post links "
        "the person with. May be one, several, or empty for a vague "
        "'wants to leave'.",
    )
    headline: str = Field(
        description="Factual headline, at most 12 words. No hype."
    )
    summary: str = Field(
        description="1-2 neutral, trader-readable sentences on what the post "
        "says about this person's move."
    )


class Assessment(BaseModel):
    """The full structured result: a list of signals (possibly empty)."""

    signals: List[Signal] = Field(default_factory=list)


SYSTEM_PROMPT = """\
You are a sports transfer-market analyst working for a betting trading desk.
You read a single social-media post and decide whether it carries any
"Next Club" / "Next Team" signal — news about where a specific PERSON (a player
OR a manager/head coach) will be next — for football (soccer) or the NBA.

Return a list of signals, one element per PERSON the post carries a transfer,
free-agency or managerial-move signal about. A single post can carry more than
one person, or none.

THE MARKET IS THE PERSON. The clubs, franchises or national teams a post
mentions are only the runners inside that person's market — record them in
`clubs`, but never treat a club as the subject. Two posts about the same person
that name different clubs are the SAME market, not a new one. Whether they stay,
leave, are bid for, rejected, appointed, or complete a move — it all belongs to
that one person's market.

BOTH players and managers count:
  - `role` = "Player" for an athlete moving (or linked with moving) club/team.
  - `role` = "Manager" for a head coach / manager moving (or linked with moving)
    to a new job — including taking over a NATIONAL TEAM. A manager appointment
    ("X to become the new [team] manager") IS a signal; tag it Manager and list
    the destination in `clubs`.

Emit a signal for a genuine move story about a player or a manager. Return an
EMPTY list for anything that is not a person changing (or being linked with
changing) club/team/job, including:
  - match reports, results, in-game performances, Summer League box scores
  - injuries, fitness, contract-length trivia with no move implied
  - polls, opinion, banter, nostalgia, "on this day", photos, tributes
Materiality is about scope, not drama: a loud all-caps rumour about a player or
manager still IS a signal (its credibility is judged later, not here); an
off-topic post is not, however senior the source.

A statement about whether a person will MOVE OR STAY is a signal — including a
club, board or source insisting they will stay, are not for sale, or dismissing
the speculation. That is a real development in that person's next-club/team/job
market (it points to "stays"), so emit a signal. Only genuinely off-topic
content (the categories above) yields nothing.

For each signal:
  - `player`: the person's full name, spelled the same way every time so the
    market key is stable. This person IS the market.
  - `role`: "Player" or "Manager".
  - `sport`: "Football" or "Basketball".
  - `clubs`: every club/franchise/national team the post links THIS person with.
    If the post only says they want to leave with no destination, leave it empty.
  - `headline`: factual, <= 12 words, no hype.
  - `summary`: 1-2 neutral sentences a trader can read at a glance.

MATCHING AN EXISTING MARKET (avoid duplicate buckets):
You are given the list of markets already open, one per person. If a signal is
about a person who ALREADY has an open market, reuse that market's EXACT `player`
spelling verbatim — do not coin a new spelling, drop or add a first name, or
change punctuation. "Julian Alvarez" and "Alvarez" are the SAME person and must
resolve to the single spelling already open. Only mint a new `player` name when
the person genuinely has no market open yet.

Base everything on the post's content and these general instructions only.
"""


def _format_open_markets(open_markets: list[str]) -> str:
    if not open_markets:
        return "(none open yet — any signal starts a new market)"
    return "\n".join(f"- {name}" for name in open_markets)


def assess_post(
    post: dict, client: OpenAI, open_markets: list[str] | None = None
) -> Assessment:
    """Run Agent 2 on one raw post. Raises on API/parse failure.

    ``post`` is a raw_posts row dict. ``open_markets`` is the list of player
    names that already have a market open, so the model reuses an existing
    spelling instead of splitting one person into two buckets. Returns a
    validated ``Assessment``.
    """
    user_content = (
        f"MARKETS ALREADY OPEN (reuse the exact spelling if this post is about "
        f"one of these people):\n"
        f"{_format_open_markets(open_markets or [])}\n\n"
        f"POST TO ASSESS\n"
        f"Author: {post.get('author', '')} "
        f"(@{post.get('source_handle', '')})\n"
        f"Posted at: {post.get('posted_at', '')}\n"
        f"Post:\n{post.get('raw_content', '')}"
    )

    completion = client.chat.completions.parse(
        model=config.MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        response_format=Assessment,
        temperature=0,
        seed=config.SEED,
    )
    parsed = completion.choices[0].message.parsed
    if parsed is None:
        raise RuntimeError("Agent 2 returned no parsed content")
    return parsed
