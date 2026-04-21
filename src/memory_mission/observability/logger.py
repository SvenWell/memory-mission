"""Append-only structured logger for audit trail.

TODO (Step 2): Implement immutable JSONL logger per firm with:
- Event schema: event_type, firm_id, employee_id, timestamp, trace_id, payload
- Append-only semantics (no truncation, no edits)
- Queryable read API for admin dashboard
- Retention policy enforcement (7 years wealth management compliance)
"""
