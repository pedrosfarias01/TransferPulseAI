"""Central configuration for TransferPulse AI.

Everything the rest of the app needs to be told — the model, the loop cadence,
the alert threshold and where the CSVs live — is defined here and nowhere else.
"""

from pathlib import Path

# --- LLM -------------------------------------------------------------------
# Model string used by both LLM agents. Swap here to change the whole pipeline.
MODEL = "gpt-4o-2024-08-06"
# Fixed seed + temperature 0 make Reprocess reproducible: the same posts
# re-score to the same signals and impacts, so a re-run never doubles or drifts.
SEED = 42

# --- Pipeline cadence ------------------------------------------------------
# How many posts Agent 1 releases from the fixture per fetch. 1 gives the
# demo a clean fetch → assess → score → fetch rhythm; raise to burst-load.
FETCH_BATCH_SIZE = 1
# Seconds to pause between pipeline ticks so a human can follow the flow.
# Adjustable at runtime via sidebar → Dev tools; this is only the default.
TICK_SLEEP_SECONDS = 4.0

# --- Trading desk ----------------------------------------------------------
# Impact score (0-100) at/above which — combined with new information — an
# alert escalates to the trading desk.
ALERT_THRESHOLD = 70
# Credibility tier assigned to any post whose handle is not in sources.csv.
DEFAULT_CREDIBILITY_TIER = 3

# --- Slack ------------------------------------------------------------------
# Incoming-webhook URL every fired alert is posted to. Overridable via the
# SLACK_WEBHOOK_URL env var so the secret can live in .env instead of source.
import os  # noqa: E402  (kept local to this small config block)

SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")

# --- Paths -----------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent

# Provided, read-only fixtures. `posts2.csv` is the current default demo
# set; earlier fixtures (posts.csv, raw_posts_filtered.csv) stay in the
# folder as fallbacks.
FIXTURES_POSTS = BASE_DIR / "fixtures" / "posts2.csv"
SOURCES_CSV = BASE_DIR / "config" / "sources.csv"

# Sportradar logo shown in the sidebar. Lives at the repo root (one level up
# from this package); fall back to a copy alongside app.py if present.
LOGO_PATH = BASE_DIR.parent / "srlogo.png"
if not LOGO_PATH.exists():
    LOGO_PATH = BASE_DIR / "srlogo.png"

# Generated at runtime; wiped by Reset.
DATA_DIR = BASE_DIR / "data"
RAW_POSTS_CSV = DATA_DIR / "raw_posts.csv"   # the live queue
EVENTS_CSV = DATA_DIR / "events.csv"          # the signals / board
