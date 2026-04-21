"""Page format: compiled truth + timeline (GBrain pattern).

TODO (Step 6): Implement:
- Page schema (YAML frontmatter + two zones separated by '---')
- Parse / serialize via python-frontmatter + markdown-it-py
- Above the line: compiled truth (rewritten on update)
- Below the line: timeline (append-only evidence with dates + sources)
- [[wikilinks]] for interlinking (Obsidian-compatible)
- Raw data sidecars (.raw/<slug>.json) for API response provenance

Rule: every compiled truth claim must trace to a timeline entry.
"""
