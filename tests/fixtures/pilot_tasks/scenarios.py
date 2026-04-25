"""Pilot-task scenario fixtures — venture-fund shaped synthetic data.

Each scenario class is a self-contained corpus + query + expected
behavior, parameterizable across whichever ``PersonalMemoryBackend``
impl is under test.

Acceptance gate (the four P0 scenarios):

- ``company_recency_summary`` — query by entity returns chronological
  hits with citations spanning email/calendar/transcripts
- ``followup_commitments`` — outbound email + transcripts surface
  action-item ``CandidateFact``s with citations
- ``last_meeting_deltas`` — last-meeting transcript hits + comparable
  prior-state hits surface so a host LLM can compose deltas
- ``pre_interaction_context`` — ``working_context()`` returns relevant
  hits + employee preferences + open commitments

Fixtures emit ``NormalizedSourceItem`` directly — bypasses the connector
layer (which lands in P3) since the contract test only exercises the
substrate.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from memory_mission.ingestion.roles import ConnectorRole, NormalizedSourceItem


@dataclass(frozen=True)
class Scenario:
    """One pilot-task scenario — corpus + query + acceptance assertions."""

    name: str
    employee_id: str
    corpus: list[NormalizedSourceItem]
    query: str
    expected_min_hits: int
    expected_entity_in_results: str | None = None
    expected_citation_count_min: int = 1


def _email(
    *,
    employee_id: str,
    external_id: str,
    title: str,
    body: str,
    when: datetime,
    direction: str = "inbound",
) -> NormalizedSourceItem:
    return NormalizedSourceItem(
        source_role=ConnectorRole.EMAIL,
        concrete_app="gmail",
        external_object_type="message",
        external_id=external_id,
        container_id=f"inbox-{employee_id}",
        url=f"https://mail.google.com/mail/u/0/#inbox/{external_id}",
        modified_at=when,
        visibility_metadata={"direction": direction},
        target_scope="public",
        target_plane="personal",
        title=title,
        body=body,
        raw={"direction": direction},
    )


def _calendar(
    *,
    employee_id: str,
    external_id: str,
    title: str,
    body: str,
    when: datetime,
    attendees: list[str],
) -> NormalizedSourceItem:
    return NormalizedSourceItem(
        source_role=ConnectorRole.CALENDAR,
        concrete_app="google_calendar",
        external_object_type="event",
        external_id=external_id,
        container_id=f"primary-{employee_id}",
        url=f"https://calendar.google.com/calendar/event?eid={external_id}",
        modified_at=when,
        visibility_metadata={"attendees": attendees},
        target_scope="public",
        target_plane="personal",
        title=title,
        body=body,
        raw={"attendees": attendees},
    )


def _transcript(
    *,
    employee_id: str,
    external_id: str,
    title: str,
    body: str,
    when: datetime,
    company: str | None = None,
) -> NormalizedSourceItem:
    return NormalizedSourceItem(
        source_role=ConnectorRole.TRANSCRIPT,
        concrete_app="granola",
        external_object_type="transcript",
        external_id=external_id,
        container_id=f"granola-{employee_id}",
        url=f"https://granola.so/transcripts/{external_id}",
        modified_at=when,
        visibility_metadata={"company": company} if company else {},
        target_scope="public",
        target_plane="personal",
        title=title,
        body=body,
        raw={"company": company} if company else {},
    )


# ---------- Scenario 1: company / contact recency summary ----------


def company_recency_summary() -> Scenario:
    """Query: 'what happened with northpoint capital recently?'

    Corpus: 3 emails + 1 calendar + 1 transcript across 3 weeks, all
    referencing the same company. Expected: ≥3 hits, company entity
    surfaced, each hit cited to its source object.
    """
    employee = "alice-vc-example"
    base = datetime(2026, 4, 1, 9, tzinfo=UTC)
    return Scenario(
        name="company_recency_summary",
        employee_id=employee,
        corpus=[
            _email(
                employee_id=employee,
                external_id="msg-001",
                title="Intro: Northpoint Capital → Acme AI",
                body=(
                    "Hi Alice, putting you in touch with Sarah Chen, partner at "
                    "Northpoint Capital. They are leading a Series B for one of our "
                    "portfolio companies and would love to compare notes."
                ),
                when=base,
                direction="inbound",
            ),
            _calendar(
                employee_id=employee,
                external_id="evt-002",
                title="Northpoint Capital + Acme AI sync",
                body=(
                    "30-min sync with Sarah from Northpoint to discuss Acme AI's "
                    "Series B terms and lead investor expectations."
                ),
                when=base + timedelta(days=4),
                attendees=["alice@vc.example", "sarah@northpoint.fund"],
            ),
            _transcript(
                employee_id=employee,
                external_id="txn-003",
                title="Northpoint sync — meeting transcript",
                body=(
                    "Sarah: Northpoint can lead at $80M post. We want a board seat "
                    "and information rights aligned with our portfolio standards. "
                    "Alice: noted. Will circle back next week with our partners."
                ),
                when=base + timedelta(days=4, hours=1),
                company="northpoint capital",
            ),
            _email(
                employee_id=employee,
                external_id="msg-004",
                title="Re: Northpoint terms",
                body=(
                    "Sarah, thanks for the call. Internal feedback: $80M post is "
                    "tighter than we'd like. Can we get to $90M with the board seat?"
                ),
                when=base + timedelta(days=11),
                direction="outbound",
            ),
            _email(
                employee_id=employee,
                external_id="msg-005",
                title="Re: Northpoint terms",
                body=(
                    "Alice — we can flex to $85M post with a 2-year board seat plus "
                    "standard pro-rata. Final from us. Talk Friday?"
                ),
                when=base + timedelta(days=18),
                direction="inbound",
            ),
        ],
        query="northpoint capital",
        expected_min_hits=3,
        expected_entity_in_results="northpoint capital",
        expected_citation_count_min=1,
    )


# ---------- Scenario 2: follow-up commitments ----------


def followup_commitments() -> Scenario:
    """Query (or candidate_facts since=...): 'what follow-up did I commit to?'

    Corpus: outbound emails + meeting transcripts where Alice promises
    deliverables. Expected: ≥2 candidate-fact-shaped commitments
    surface with citations, employee_id matches.
    """
    employee = "alice-vc-example"
    base = datetime(2026, 4, 8, 9, tzinfo=UTC)
    return Scenario(
        name="followup_commitments",
        employee_id=employee,
        corpus=[
            _email(
                employee_id=employee,
                external_id="msg-101",
                title="Re: Helix Bio diligence",
                body=(
                    "Hi team — I'll share the updated competitive landscape memo by "
                    "Thursday EOD. Let me know if you need it sooner."
                ),
                when=base,
                direction="outbound",
            ),
            _transcript(
                employee_id=employee,
                external_id="txn-102",
                title="Helix Bio partner meeting transcript",
                body=(
                    "Alice: I'll own the primary research interviews — three founders "
                    "by next Wednesday. Mark, you've got the financial model rebuild?"
                ),
                when=base + timedelta(days=2),
                company="helix bio",
            ),
            _email(
                employee_id=employee,
                external_id="msg-103",
                title="Following up — Helix landscape memo",
                body=(
                    "Sending the competitive landscape memo as promised. Three "
                    "primary-research interviews completed yesterday; notes attached."
                ),
                when=base + timedelta(days=4),
                direction="outbound",
            ),
        ],
        query="follow up commitments",
        expected_min_hits=1,
        expected_entity_in_results=None,
        expected_citation_count_min=1,
    )


# ---------- Scenario 3: last-meeting deltas ----------


def last_meeting_deltas() -> Scenario:
    """Query: 'what was said in the last meeting with helix bio and what changed?'

    Corpus: two transcripts with helix bio, three weeks apart, with
    measurable state changes (revenue, headcount, new hire).
    Expected: ≥2 hits, both transcripts surface, host LLM can compose
    deltas from cited content.
    """
    employee = "bob-vc-example"
    base = datetime(2026, 3, 1, 14, tzinfo=UTC)
    return Scenario(
        name="last_meeting_deltas",
        employee_id=employee,
        corpus=[
            _transcript(
                employee_id=employee,
                external_id="txn-201",
                title="Helix Bio Q1 update",
                body=(
                    "Founder: Q1 closed at $1.2M ARR, up from $800K end of last year. "
                    "Headcount is 14, hiring two more engineers in April. Series A "
                    "runway through end of 2027 at current burn."
                ),
                when=base,
                company="helix bio",
            ),
            _transcript(
                employee_id=employee,
                external_id="txn-202",
                title="Helix Bio April update",
                body=(
                    "Founder: April closed at $1.6M ARR. Headcount now 17 — added two "
                    "engineers and a head of GTM, which is the new hire we discussed "
                    "last time. Burn went up 30% with the GTM hire; runway now ends "
                    "Q3 2027 at current pace."
                ),
                when=base + timedelta(days=42),
                company="helix bio",
            ),
        ],
        query="helix bio recent",
        expected_min_hits=2,
        expected_entity_in_results="helix bio",
        expected_citation_count_min=1,
    )


# ---------- Scenario 4: pre-interaction private context ----------


def pre_interaction_context() -> Scenario:
    """working_context(employee_id, task) returns hits + preferences + commitments.

    Corpus: relevant prior hits for an upcoming meeting with Sarah at
    Northpoint Capital. Expected: ``WorkingContext`` returns ≥1
    relevant hit; ``open_commitments`` includes the outstanding
    'circle back next week' from txn-003 above (in a real impl);
    ``employee_preferences`` may be empty for synthetic fixture.
    """
    employee = "alice-vc-example"
    base = datetime(2026, 4, 22, 9, tzinfo=UTC)
    return Scenario(
        name="pre_interaction_context",
        employee_id=employee,
        corpus=[
            _email(
                employee_id=employee,
                external_id="msg-301",
                title="Re: Friday call",
                body=(
                    "Confirming Friday 4pm for the follow-up on Northpoint terms. "
                    "I'll have my response on the $85M post + board seat ready."
                ),
                when=base,
                direction="outbound",
            ),
            _calendar(
                employee_id=employee,
                external_id="evt-302",
                title="Northpoint Friday follow-up",
                body=(
                    "Friday 4pm — Northpoint follow-up call. Alice prepares response "
                    "on $85M post + 2-year board seat from Northpoint."
                ),
                when=base + timedelta(days=2),
                attendees=["alice@vc.example", "sarah@northpoint.fund"],
            ),
        ],
        query="northpoint friday call",
        expected_min_hits=1,
        expected_entity_in_results="northpoint",
        expected_citation_count_min=1,
    )


ALL_SCENARIOS = [
    company_recency_summary,
    followup_commitments,
    last_meeting_deltas,
    pre_interaction_context,
]
