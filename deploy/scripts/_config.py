"""Shared config for deploy/scripts/*.

All operational constants come from environment variables — the same scripts
work for any deployment, not just one author's setup.

Required env:
  MM_USER_ID        employee/user identity in memory-mission (e.g. "alice")
  MM_FIRM_ROOT      path to firm root data dir (e.g. /root/memory-mission-data)

Optional env:
  MM_FIRM_ID        firm identity; defaults to MM_USER_ID for solo deployments
  MM_GMAIL_ACCOUNTS    "label:composio-user-id,label:composio-user-id"
  MM_CALENDAR_ACCOUNTS same format as MM_GMAIL_ACCOUNTS
  MM_GRANOLA_USER_ID   composio connection user_id for Granola
  MM_VIZ_CENTERS    comma-separated entity ids for visualize_kg ego views
  MM_VIZ_OUT_DIR    visualize_kg output dir (defaults to <FIRM_ROOT>/kg_viz)

Set values via process env (cron) or by sourcing a deploy/.env.local file
before invoking the scripts. See deploy/.env.example for a template.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _required(key: str) -> str:
    value = os.environ.get(key)
    if not value:
        sys.stderr.write(
            f"missing required env var {key}\n"
            f"see deploy/.env.example for the full env contract\n"
        )
        raise SystemExit(2)
    return value


# --- Identity ----------------------------------------------------------------

EMPLOYEE: str = _required("MM_USER_ID")
FIRM_ID: str = os.environ.get("MM_FIRM_ID") or EMPLOYEE


# --- Paths -------------------------------------------------------------------

FIRM_ROOT: Path = Path(_required("MM_FIRM_ROOT")).expanduser()
WIKI_ROOT: Path = FIRM_ROOT / "wiki"
OBS_ROOT: Path = FIRM_ROOT / ".observability"
DURABLE_DB: Path = FIRM_ROOT / "durable.sqlite3"
STAGING: Path = WIKI_ROOT / "staging" / "personal" / EMPLOYEE
STAGING_FACTS: Path = STAGING / ".facts"


# --- Helpers ----------------------------------------------------------------

def parse_accounts(env_var: str) -> dict[str, str]:
    """Parse 'label:user_id,label:user_id' env into {label: user_id}.

    Returns {} if the env var is unset or empty.
    """
    raw = os.environ.get(env_var, "").strip()
    if not raw:
        return {}
    out: dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair or ":" not in pair:
            continue
        label, _, user_id = pair.partition(":")
        out[label.strip()] = user_id.strip()
    return out


def viz_centers() -> list[str]:
    raw = os.environ.get("MM_VIZ_CENTERS", "").strip()
    return [c.strip() for c in raw.split(",") if c.strip()]


def viz_out_dir() -> Path:
    return Path(os.environ.get("MM_VIZ_OUT_DIR") or (FIRM_ROOT / "kg_viz"))
