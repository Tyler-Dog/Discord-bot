"""Docker HEALTHCHECK: exit 0 only if the bot wrote a fresh heartbeat (gateway connected and loop alive)."""
import os
import sys
import time
from pathlib import Path

MAX_AGE = 120  # seconds

path = Path(os.getenv("DATA_DIR", Path(__file__).resolve().parent.parent / "data")) / ".heartbeat"
try:
    age = time.time() - float(path.read_text())
except (OSError, ValueError):
    sys.exit(1)
sys.exit(0 if age < MAX_AGE else 1)
