"""Single source of truth for the VPS FINAL research-to-lock contract."""
from datetime import time

# Only a report written by news_collector.vps_final carries this marker.
PIPELINE_VERSION = "vps-codex-final-v1"
FINAL_CUTOFF = time(9, 0)
FINAL_DEADLINE = time(9, 12)
WATCHLIST_LOCK = time(9, 15)
