"""Pydantic models for the distilled agent context package.

``AgentContext`` is the top-level output of ``compile_agent_context``.
Structured so the eval harness can grade it without parsing prose
(``docs/EVALS.md`` section 2.8). ``render()`` produces the
markdown string the host-agent LLM actually sees.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from memory_mission.memory.knowledge_graph import CoherenceWarning, Triple
from memory_mission.memory.pages import Page
from memory_mission.memory.schema import Plane
from memory_mission.memory.tiers import Tier

if TYPE_CHECKING:
    pass


class AttendeeContext(BaseModel):
    """Everything the firm currently believes about one attendee.

    Shape mirrors Tolaria's Neighborhood mode: outgoing relationships,
    incoming (inverse) relationships, events + preferences as their
    own groups, related pages (curated documents). Empty groups stay
    visible with count 0 so reviewers see "we have nothing on this
    person" instead of "we forgot to check."
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    attendee_id: str
    canonical_name: str | None = None
    outgoing_triples: list[Triple] = Field(default_factory=list)
    incoming_triples: list[Triple] = Field(default_factory=list)
    events: list[Triple] = Field(default_factory=list)
    preferences: list[Triple] = Field(default_factory=list)
    related_pages: list[Page] = Field(default_factory=list)
    coherence_warnings: list[CoherenceWarning] = Field(default_factory=list)

    @property
    def fact_count(self) -> int:
        return (
            len(self.outgoing_triples)
            + len(self.incoming_triples)
            + len(self.events)
            + len(self.preferences)
        )

    @property
    def display_name(self) -> str:
        """The attendee's readable name, falling back to the ID."""
        return self.canonical_name or self.attendee_id


class DoctrineContext(BaseModel):
    """Firm-level authoritative context: constitution + doctrine + policy pages.

    Filtered by ``tier_floor`` in the parent ``AgentContext``. When
    the floor is ``None``, pages is empty — the caller explicitly
    opted out of doctrine context. When the floor is set, the listed
    pages are those at or above that floor, sorted by tier descending
    (constitution first) then slug ascending.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    pages: list[Page] = Field(default_factory=list)

    @property
    def page_count(self) -> int:
        return len(self.pages)


class AgentContext(BaseModel):
    """Distilled context package compiled for one role + task.

    Produced by ``compile_agent_context``. Consumed by workflow skills
    that render it into the LLM prompt. The structured form is the
    authority — ``render()`` is a convenience.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    role: str  # "meeting-prep", "email-draft", ...
    task: str
    plane: Plane
    as_of: date | None = None
    tier_floor: Tier | None = None
    attendees: list[AttendeeContext] = Field(default_factory=list)
    doctrine: DoctrineContext = Field(default_factory=DoctrineContext)
    generated_at: datetime

    @property
    def fact_count(self) -> int:
        """Sum of facts across all attendees — useful for budget / eval."""
        return sum(a.fact_count for a in self.attendees)

    @property
    def attendee_ids(self) -> list[str]:
        return [a.attendee_id for a in self.attendees]

    def render(self) -> str:
        """Render the package as markdown for the host-agent LLM.

        Sections in order:

        1. Header: role + task + as_of (if set)
        2. Doctrine (if ``tier_floor`` set and pages exist)
        3. Per-attendee sections: name, outgoing, incoming, events,
           preferences, related pages. Empty groups render as
           ``(none on file)`` so the LLM sees the absence explicitly.
        4. Footer: generation timestamp

        Every triple is cited inline with ``[source_closet/source_file]``
        so downstream drafts can attribute claims.
        """
        lines: list[str] = []
        lines.append(f"# Context for {self.role}")
        lines.append(f"**Task:** {self.task}")
        lines.append(f"**Plane:** `{self.plane}`")
        if self.as_of is not None:
            lines.append(f"**As of:** {self.as_of.isoformat()}")
        if self.tier_floor is not None:
            lines.append(f"**Tier floor:** `{self.tier_floor}`")
        lines.append("")

        if self.doctrine.pages:
            lines.append("## Firm doctrine")
            for page in self.doctrine.pages:
                lines.append(
                    f"- **{page.frontmatter.title}** "
                    f"(`{page.frontmatter.tier}`, domain `{page.domain}`)"
                )
                if page.compiled_truth.strip():
                    lines.append(f"  > {_one_line(page.compiled_truth)}")
            lines.append("")

        if not self.attendees:
            lines.append("## Attendees")
            lines.append("_No attendees specified._")
        else:
            lines.append("## Attendees")
            for attendee in self.attendees:
                lines.extend(_render_attendee(attendee))

        lines.append("")
        lines.append(
            f"_Compiled at {self.generated_at.isoformat()} · "
            f"{self.fact_count} facts across {len(self.attendees)} attendees._"
        )
        return "\n".join(lines)


# ---------- Rendering helpers (module-level, not methods on Pydantic) ----------


def _render_attendee(attendee: AttendeeContext) -> list[str]:
    lines: list[str] = []
    lines.append(f"### {attendee.display_name} (`{attendee.attendee_id}`)")

    if attendee.coherence_warnings:
        lines.append("")
        lines.append("> [!contradiction] Unresolved tier conflict")
        for w in attendee.coherence_warnings:
            new_ref = f"`{w.subject} {w.predicate} = {w.new_object}` ({w.new_tier})"
            old_ref = f"`{w.subject} {w.predicate} = {w.conflicting_object}` ({w.conflicting_tier})"
            lines.append(f"> - {new_ref} vs {old_ref}")

    lines.append("")
    lines.append("**Outgoing relationships**")
    lines.extend(_render_triple_list(attendee.outgoing_triples, direction="outgoing"))

    lines.append("")
    lines.append("**Incoming relationships**")
    lines.extend(_render_triple_list(attendee.incoming_triples, direction="incoming"))

    lines.append("")
    lines.append("**Recent events**")
    lines.extend(_render_triple_list(attendee.events, direction="event"))

    lines.append("")
    lines.append("**Preferences**")
    lines.extend(_render_triple_list(attendee.preferences, direction="preference"))

    if attendee.related_pages:
        lines.append("")
        lines.append("**Related pages**")
        for page in attendee.related_pages:
            lines.append(
                f"- [[{page.frontmatter.slug}]] — {page.frontmatter.title} "
                f"(`{page.frontmatter.tier}`)"
            )

    lines.append("")
    return lines


def _render_triple_list(triples: list[Triple], *, direction: str) -> list[str]:
    if not triples:
        return ["_(none on file)_"]
    lines: list[str] = []
    for t in triples:
        if direction == "event" and t.valid_from is not None:
            prefix = f"{t.valid_from.isoformat()} — "
        else:
            prefix = ""
        if direction == "preference":
            core = f"prefers **{t.object}**"
        elif direction == "incoming":
            core = f"**{t.subject}** {t.predicate} → {t.object}"
        elif direction == "event":
            core = f"{t.object}"
        else:
            core = f"{t.predicate} → **{t.object}**"
        provenance = _provenance_cite(t)
        conf = f" _(confidence {t.confidence:.2f})_" if t.confidence < 0.99 else ""
        lines.append(f"- {prefix}{core}{conf} {provenance}")
    return lines


def _provenance_cite(triple: Triple) -> str:
    """Inline citation like ``[firm/contracts/2026-q1.md]``.

    Covers the most recent source from the triples row; full
    ``triple_sources`` history is available via
    ``KnowledgeGraph.triple_sources`` when the caller needs more.
    Absolute paths have their leading ``/`` stripped so the rendered
    citation reads as a logical path (``firm/sources/foo.md``) rather
    than a filesystem path (``firm//sources/foo.md``).
    """
    if triple.source_closet and triple.source_file:
        file = triple.source_file.lstrip("/")
        return f"`[{triple.source_closet}/{file}]`"
    if triple.source_closet:
        return f"`[{triple.source_closet}]`"
    return ""


def _one_line(text: str, *, limit: int = 200) -> str:
    """Collapse text to a single line for doctrine summaries."""
    collapsed = " ".join(text.split())
    if len(collapsed) > limit:
        return collapsed[: limit - 1] + "…"
    return collapsed
