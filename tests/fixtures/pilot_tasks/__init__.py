"""Pilot-task fixtures — venture/PE/wealth-shaped acceptance scenarios.

The four scenario classes (per the P0 contract):

1. **Company / contact recency summary** — "what happened with this
   company recently?" Query by entity, get a chronologically ordered +
   cited summary across email + calendar + transcripts.
2. **Follow-up commitments** — "what follow-up did I commit to?"
   Extract action-item-shaped facts from the employee's own outbound
   email + meeting transcripts.
3. **Last-meeting deltas** — "what was said in the last meeting and
   what changed?" Pull last meeting transcript for an attendee, surface
   deltas vs prior state.
4. **Pre-interaction private context** — "what private context should
   inform this next interaction?" Pre-meeting brief that includes
   private-only facts + the employee's preferences + their open
   commitments.

These fixtures are **synthetic** but firm-shaped — venture-fund-style
deal-flow + portfolio-tracking + LP-update language. NOT Sven's
personal data. Per the revised plan: validation uses sandbox firm data,
not personal dogfood.
"""
