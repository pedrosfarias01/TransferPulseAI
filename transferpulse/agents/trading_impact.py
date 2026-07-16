"""Agent 3 — Trading Impact & Alert.

Reads one unpriced signal plus its market's existing timeline and the source's
credibility tier, and in a single structured LLM call decides how hot the
signal is, whether it adds new information, and what the desk should do.

Framed as a trading analyst deciding what to escalate: score the delta, not the
drama. Novelty gates the alert; a tier-4 megaphone stays quiet.
"""

from __future__ import annotations

from typing import Literal

from openai import OpenAI
from pydantic import BaseModel, Field

import config


class ImpactAssessment(BaseModel):
    """Structured trading-impact verdict for one signal."""

    is_new_information: bool = Field(
        description="Does this add something the market's timeline does not "
        "already contain? A repeat, rehash or weaker echo is false."
    )
    impact_score: int = Field(
        ge=0, le=100,
        description="0-100, how HOT the signal is: combine novelty, source "
        "credibility and magnitude. Old news is low even if dramatic; a "
        "tier-4 aggregator is low however loud.",
    )
    impact_rationale: str = Field(
        description="One sentence: why this score."
    )
    suggested_action: Literal[
        "Create new content",
        "Suspend and Adjust Prices",
        "No changes needed",
        "Suspend and Review for Late Bets",
    ] = Field(
        description="'Create new content' for the first credible rumour; "
        "'Suspend and Review for Late Bets' for a confirmed completed deal; "
        "otherwise 'Suspend and Adjust Prices' or 'No changes needed'."
    )


SYSTEM_PROMPT = """\
You are a trading analyst at a sports-betting desk covering "Next Club" /
"Next Team" markets (where a player signs next). For each incoming signal you
decide what, if anything, to escalate to the traders.

You are given:
  - the NEW signal (a player, the clubs named, a headline and summary),
  - the SOURCE'S credibility tier (1 = tier-1 insider whose word moves the
    market; 4 = unverified fan aggregator, frequently wrong, high drama) and a
    note about it,
  - the market's EXISTING TIMELINE: the signals already priced for this same
    player, oldest first. This is what tells you whether the new signal is
    actually new.

SCORE THE DELTA, NOT THE DRAMA.
  - Novelty: does this move the story beyond what the timeline already says? A
    dry, specific development ("club will negotiate with X but will not sell to
    Y") is hot and new. The fifth "still no bid / nothing has changed" post is
    neither — even from a tier-1 source.
  - Credibility: weight the source. A tier-1 insider breaking real news scores
    high. A tier-4 account shouting "DONE DEAL" in all caps is low impact
    however loud — treat unverified hype as noise.
  - Magnitude: does it shift the whole market (a player entering free agency, a
    completed transfer, a decisive "we will not sell to them"), or just add
    another interested name?

Set the fields:
  - `is_new_information`: true only if it genuinely adds to the timeline.
    Re-reporting something already on the timeline is false.
  - `impact_score` (0-100): hot + new + credible is high; repeats and
    low-credibility noise are low.
  - `impact_rationale`: one sentence explaining the score.
  - `suggested_action`:
      * "Create new content"            — the first credible rumour that a
        player may move (no meaningful prior timeline yet).
      * "Suspend and Adjust Prices"     — a new, credible development in an
        already-open market.
      * "No changes needed"             — nothing new, or too weak/unverified
        to act on.
      * "Suspend and Review for Late Bets" — a confirmed, completed deal; the
        market resolves.

Judge only from the signal, the timeline and the credibility tier given.
"""


def _format_timeline(timeline: list[dict]) -> str:
    if not timeline:
        return "(no prior signals — this is the first for this player)"
    lines = []
    for ev in timeline:
        clubs = ev.get("clubs", "") or "-"
        score = ev.get("impact_score", "") or "?"
        lines.append(
            f"- {ev.get('event_date', '')} | {ev.get('headline', '')} "
            f"| clubs: {clubs} | prior impact: {score}"
        )
    return "\n".join(lines)


def score_event(
    event: dict,
    timeline: list[dict],
    credibility_tier: int,
    credibility_note: str,
    client: OpenAI,
) -> tuple[ImpactAssessment, bool]:
    """Run Agent 3 on one unpriced event.

    ``timeline`` is the list of already-priced events for the same player,
    oldest first. Returns the validated assessment and the computed ``alert``
    flag. Raises on API/parse failure.
    """
    user_content = (
        f"NEW SIGNAL\n"
        f"Player (market): {event.get('player', '')}\n"
        f"Sport: {event.get('sport', '')}\n"
        f"Clubs named: {event.get('clubs', '') or '-'}\n"
        f"Headline: {event.get('headline', '')}\n"
        f"Summary: {event.get('summary_llm', '')}\n"
        f"Date: {event.get('event_date', '')}\n\n"
        f"SOURCE CREDIBILITY\n"
        f"Tier: {credibility_tier} (1 best, 4 worst)\n"
        f"Note: {credibility_note}\n\n"
        f"EXISTING TIMELINE FOR THIS PLAYER (oldest first)\n"
        f"{_format_timeline(timeline)}"
    )

    completion = client.chat.completions.parse(
        model=config.MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        response_format=ImpactAssessment,
        temperature=0,
        seed=config.SEED,
    )
    parsed = completion.choices[0].message.parsed
    if parsed is None:
        raise RuntimeError("Agent 3 returned no parsed content")

    # The alert rule is a deterministic policy the desk owns — enforce it in
    # code rather than trusting the model to apply the threshold.
    alert = bool(
        parsed.impact_score >= config.ALERT_THRESHOLD
        and parsed.is_new_information
    )
    return parsed, alert
