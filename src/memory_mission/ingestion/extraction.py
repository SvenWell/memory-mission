"""Component 1.2 — Real-Time Extraction Agent.

TODO (Step 8, Phase 2): When a new interaction arrives (transcript, email, meeting),
extract firm-relevant facts and propose updates to the firm wiki.

Extraction taxonomy (adapted from Supermemory's 6-vector framework):
1. Client Profile (name, role, firm, AUM, preferences)
2. Investment Preferences (risk appetite, sector interests, allocation targets)
3. Commitments & Action Items (promises, deadlines, follow-ups)
4. Relationship Dynamics (sentiment, trust level, concerns)
5. Deal Progress (stage changes, terms discussed, blockers)
6. Market Observations (client market views, regulatory concerns, competitive intel)

Contradiction resolution: state mutations (supersede) vs refinements (extend).
All extractions go to staging area with confidence scores.
"""
