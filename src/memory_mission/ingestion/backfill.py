"""Component 1.1 — Backfill Agent (historical data).

TODO (Step 7): Port Rowboat's sync_gmail.ts + sync_calendar.ts patterns to Python:
- google-api-python-client for Gmail/Calendar
- O365 or msal for Microsoft Graph
- Sequential processing, chronological order, one employee at a time
- Idempotent: _backfill/processed.jsonl tracks processed IDs
- Checkpointed via component 0.6 (resume on crash — critical for 24h+ runs)
- GBrain enrichment tiers (1/3/8+ mentions)
- Output: staging area for human review before entering firm wiki
"""
