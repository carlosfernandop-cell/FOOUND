# FOOUND — Ownership Map

*Every client action × who owns it today × who must own it in V1.
Frozen August 21, 2026. The "today" column reaching all-No-Carlos is the
definition of done for the №003 milestone. Update this file as ownership
actually moves — it is the honest scoreboard, not a wish.*

| Action | Owner today | Required V1 owner | Change needed |
|---|---|---|---|
| Accept invitation / authenticate | — (no flow) | Client + system | Invitation flow (P3) |
| Recover access (magic link resend) | Carlos (rate-limit workarounds) | Client + system | Custom SMTP + retry flows (P1) |
| Upload / delete evidence | — (email to Carlos) | Client | Feed + Storage (P3) |
| Add links | — (email) | Client | Feed (P3) |
| Establish role direction | Carlos (sessions) | FOOUND proposes; client confirms | Synthesis + Mirror (P2–P3) |
| Derive title/query vocabulary | Carlos (config authoring) | FOOUND compiler | Role Model compiler (P2) |
| Establish market ("companies that matter") | Carlos (SCRAPERS edits) | FOOUND proposes; client corrects | Sources-as-data (P1) + proposal (P2) |
| Generate / correct Mirror | Carlos (hand-written Memory room) | FOOUND + client | Synthesis (P2) + Mirror page (P3) |
| Generate / correct Brief | Carlos (in-code config) | FOOUND + client | Brief object + compiler (P2–P3) |
| Determine market readiness | Carlos (judgment) | System | Readiness calculator (P2) |
| Resolve readiness failures | Carlos | Client through product | Recovery paths (P3) |
| Commission (activate) | Carlos (workflow run) | Client invokes; DB gates | commission_agent() (P1) + surface (P3) |
| Receive first edition | Carlos (public publish) | System, private, signed-in | editions + reader (P1, P3) |
| Cast verdicts (PASS/APPLIED/LOOK AGAIN) | **Client — done** | Client | None (verified live for №001) |
| Change geography / preferences / avoids | Carlos (config edits) | Client through Brief | Brief edits + recompile (P3) |
| Understand agent state (active? edition ok? waiting on me?) | Carlos explains | Client from product | Owner surface states (P3) |
| Publish / unpublish Candidate | Carlos (repo upload) | Client | Candidate publication (P5) |
| Pause / resume | Carlos (DB/state) | Client invokes; DB gates | pause/resume functions (P1) + surface (P3) |
| Archive / leave | — | Client | archive_agent() (P1) + surface (P3) |
| Recover from onboarding failures | Carlos (chat) | Client through product | §7 recovery set (P3) |
