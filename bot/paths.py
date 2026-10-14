"""Every path the bot uses, resolved relative to the project folder."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CREDENTIALS = ROOT / "credentials"
CONFIG = ROOT / "config.json"
CONFIG_EXAMPLE = ROOT / "config.example.json"

LOGS = ROOT / "logs"
STATE = ROOT / "state"
PROFILES = ROOT / "browser_profiles"
DEBUG = ROOT / "debug"


def ensure_dirs() -> None:
    for d in (LOGS, STATE, PROFILES):
        d.mkdir(exist_ok=True)
