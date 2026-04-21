"""Page format — compiled truth + timeline (GBrain pattern).

Every memory page is a markdown file with this structure:

    ---
    slug: sarah-chen
    title: Sarah Chen
    domain: people
    aliases: [Sarah, S. Chen]
    sources: [interaction-1, interaction-2]
    valid_from: 2024-01-01
    valid_to: null
    confidence: 0.95
    ---

    Sarah is the CEO of [[acme-corp]]. She prefers direct, numbers-heavy
    communication. Took over from the founder in 2023.

    ---

    2026-04-15 [interaction-2]: Confirmed CEO role in board meeting transcript
    2026-04-10 [interaction-1]: First mention as "CEO of Acme" in Granola call

Two zones separated by ``---`` on its own line:

- **Above the line: compiled truth.** Current-state narrative, rewritten on
  update. Every claim here traces to a timeline entry.
- **Below the line: timeline.** Append-only evidence log. Newest entries at
  the top. Entry format: ``YYYY-MM-DD [source-id]: text``.

``[[wikilinks]]`` inside compiled truth link to other pages by slug; the
optional ``|display`` suffix sets link text (``[[acme-corp|Acme Corp]]``).

Validity windows (``valid_from`` / ``valid_to``) mark when a fact is / was
true. ``valid_to = None`` means currently true. Staleness detection checks
``valid_from`` age against a policy.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import UTC, date, datetime
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

# Regex that matches a line of exactly three dashes (zone separator). The
# page-level separator must be a LINE by itself so frontmatter parsing
# doesn't collide with it.
_ZONE_SEP = re.compile(r"^---\s*$", re.MULTILINE)

# ``[[slug]]`` or ``[[slug|display text]]``. Captures the slug.
_WIKILINK = re.compile(r"\[\[([^\]|]+?)(?:\|[^\]]*)?\]\]")

# Timeline entry: ``YYYY-MM-DD [source-id]: text`` (allow trailing whitespace).
_TIMELINE_ENTRY = re.compile(
    r"^(\d{4}-\d{2}-\d{2})\s+\[([^\]]+)\]:\s*(.*?)\s*$",
)


class TimelineEntry(BaseModel):
    """One dated entry in a page's timeline zone."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    entry_date: date
    source_id: str
    text: str

    def render(self) -> str:
        return f"{self.entry_date.isoformat()} [{self.source_id}]: {self.text}"


class PageFrontmatter(BaseModel):
    """Structured frontmatter fields.

    Unknown fields are preserved on ``extra`` so hand-edited pages with
    custom keys survive round-trip. The core fields below are validated and
    used by the engine / search layer.
    """

    model_config = ConfigDict(extra="allow")

    slug: str
    title: str
    domain: str
    aliases: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    valid_from: date | None = None
    valid_to: date | None = None
    confidence: float = 1.0
    created: datetime | None = None
    updated: datetime | None = None

    @field_validator("confidence")
    @classmethod
    def _confidence_range(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError(f"confidence must be in [0, 1], got {v}")
        return v

    @field_validator("slug")
    @classmethod
    def _slug_shape(cls, v: str) -> str:
        if not v or not _SLUG_RE.fullmatch(v):
            raise ValueError(
                f"slug {v!r} must match {_SLUG_RE.pattern} (lowercase "
                "alphanumerics + hyphen, 1-128 chars)"
            )
        return v


_SLUG_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,126}[a-z0-9])?")


class Page(BaseModel):
    """Parsed page: frontmatter + compiled truth + timeline."""

    model_config = ConfigDict(extra="forbid")

    frontmatter: PageFrontmatter
    compiled_truth: str = ""
    timeline: list[TimelineEntry] = Field(default_factory=list)

    @property
    def slug(self) -> str:
        return self.frontmatter.slug

    @property
    def domain(self) -> str:
        return self.frontmatter.domain

    def wikilinks(self) -> list[str]:
        """Slugs referenced in the compiled-truth zone via ``[[...]]`` links."""
        return _extract_wikilinks(self.compiled_truth)

    def with_timeline_entry(self, entry: TimelineEntry) -> Page:
        """Return a new page with ``entry`` prepended to the timeline.

        Timeline is append-newest-first so readers see recent evidence first.
        The original page is unchanged (Pydantic models are effectively
        immutable via ``model_copy``).
        """
        return self.model_copy(update={"timeline": [entry, *self.timeline]})

    def render(self) -> str:
        """Serialize back to the on-disk markdown format."""
        return render_page(self)


def _extract_wikilinks(text: str) -> list[str]:
    """Return wikilink slugs in order of appearance, de-duplicated."""
    seen: dict[str, None] = {}
    for m in _WIKILINK.finditer(text):
        seen.setdefault(m.group(1).strip(), None)
    return list(seen)


def parse_page(raw: str) -> Page:
    """Parse a markdown page into a ``Page`` object.

    Expects the on-disk format: optional YAML frontmatter between ``---``
    fences, followed by compiled truth, followed by another ``---`` line,
    followed by timeline entries (one per line).

    A page with no timeline zone parses to an empty timeline. A page with no
    frontmatter raises — frontmatter is required for slug + domain.
    """
    fm_dict, body = _split_frontmatter(raw)
    fm = PageFrontmatter(**fm_dict)

    compiled_truth, timeline_text = _split_body_zones(body)
    timeline = _parse_timeline(timeline_text)

    return Page(
        frontmatter=fm,
        compiled_truth=compiled_truth.strip(),
        timeline=timeline,
    )


def render_page(page: Page) -> str:
    """Serialize a page to the on-disk markdown format."""
    fm_data = page.frontmatter.model_dump(mode="json", exclude_none=True)
    # Preserve key order: core fields first, then extras.
    ordered_keys = [
        "slug",
        "title",
        "domain",
        "aliases",
        "sources",
        "valid_from",
        "valid_to",
        "confidence",
        "created",
        "updated",
    ]
    ordered: dict[str, Any] = {}
    for key in ordered_keys:
        if key in fm_data:
            ordered[key] = fm_data[key]
    for key, value in fm_data.items():
        if key not in ordered:
            ordered[key] = value

    fm_yaml = yaml.safe_dump(ordered, sort_keys=False, allow_unicode=True).rstrip()
    parts = [
        "---",
        fm_yaml,
        "---",
        "",
        page.compiled_truth.rstrip(),
        "",
    ]
    if page.timeline:
        parts.extend(
            [
                "---",
                "",
                *(entry.render() for entry in page.timeline),
                "",
            ]
        )
    return "\n".join(parts)


def _split_frontmatter(raw: str) -> tuple[dict[str, Any], str]:
    """Extract the YAML frontmatter block. Returns ``(fm_dict, body)``."""
    if not raw.startswith("---"):
        raise ValueError("Page is missing YAML frontmatter. Expected a leading '---' line.")
    # Skip the first line (opening fence).
    rest = raw.split("\n", 1)[1] if "\n" in raw else ""
    match = _ZONE_SEP.search(rest)
    if match is None:
        raise ValueError("Page frontmatter has no closing '---' line.")
    fm_text = rest[: match.start()]
    body = rest[match.end() :].lstrip("\n")
    loaded = yaml.safe_load(fm_text) or {}
    if not isinstance(loaded, dict):
        raise ValueError("Page frontmatter must parse to a mapping.")
    return loaded, body


def _split_body_zones(body: str) -> tuple[str, str]:
    """Split body into compiled_truth and timeline_text on the ``---`` line.

    If there is no zone separator the whole body is compiled truth and the
    timeline is empty.
    """
    match = _ZONE_SEP.search(body)
    if match is None:
        return body, ""
    return body[: match.start()], body[match.end() :]


def _parse_timeline(text: str) -> list[TimelineEntry]:
    """Parse timeline entries. Skips blank lines and headings."""
    entries: list[TimelineEntry] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "//")):
            continue
        m = _TIMELINE_ENTRY.match(stripped)
        if m is None:
            # Permissive: ignore malformed lines so hand-edited pages don't
            # blow up. The engine can surface these as lint warnings later.
            continue
        entries.append(
            TimelineEntry(
                entry_date=date.fromisoformat(m.group(1)),
                source_id=m.group(2),
                text=m.group(3),
            )
        )
    return entries


def new_page(
    *,
    slug: str,
    title: str,
    domain: str,
    compiled_truth: str = "",
    timeline: Iterable[TimelineEntry] = (),
    aliases: Iterable[str] = (),
    sources: Iterable[str] = (),
    valid_from: date | None = None,
    valid_to: date | None = None,
    confidence: float = 1.0,
) -> Page:
    """Convenience constructor that stamps ``created`` / ``updated`` now."""
    now = datetime.now(UTC)
    fm = PageFrontmatter(
        slug=slug,
        title=title,
        domain=domain,
        aliases=list(aliases),
        sources=list(sources),
        valid_from=valid_from,
        valid_to=valid_to,
        confidence=confidence,
        created=now,
        updated=now,
    )
    return Page(
        frontmatter=fm,
        compiled_truth=compiled_truth,
        timeline=list(timeline),
    )
