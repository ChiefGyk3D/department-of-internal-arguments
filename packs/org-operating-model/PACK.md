---
name: "org-operating-model"
description: "Load when planning how teams, approvals, on-call, reviews and metrics fit together: layers of independence, RACI for nine processes, small-team substitutes, separation of duties, mode-to-role map."
updated: "2026-11-25"
---

# Pack: organization operating model

Status: pilot pack, written 2026-11-25. Most of this is a design synthesis, labeled INFERRED where it is one; only the incident roles, on-call numbers and the CSF Govern function are read on publisher pages (Sources). That the pack improves planning is a prediction until the measurement in "How to verify" is run.

A healthy organization separates who decides, who builds and runs, who checks and who assures, and it writes down who is accountable for each process. A small team cannot staff every layer, so it keeps the separation of decisions and replaces missing people with compensating controls. This pack is the vocabulary for that; it does not set policy for anyone.

## Rules

1. **Layers of independence (INFERRED).** Govern: the people who accept risk and fund the work; NIST CSF 2.0 added Govern as its own Function for this reason (Sources). First line: build and run (engineering, operations, service desk, delivery). Second line: oversee and challenge (risk, compliance, security review). Third line: assure the whole system to the governing body (internal or external audit). One function may sit in two layers; the rule is that one person or agent is not on both sides of one decision. The published Three Lines model was not read on its page (Sources), so the layer names here are this pack's, not a quotation.
2. **The human is the approver of record.** An agent proposes, verifies, challenges or assesses; a human approves, in their own message. Approval is as wide as its words.
3. **RACI for nine processes (INFERRED).** Columns: BO business or service owner, SEC security, OPS IT operations, ENG engineering, GRC risk and compliance, IA internal audit, MGT management. R responsible, A accountable, C consulted, I informed; each row has exactly one A.

| Process | BO | SEC | OPS | ENG | GRC | IA | MGT | Escalation |
|---|---|---|---|---|---|---|---|---|
| Access request and joiner-mover-leaver | A | C | R | C | C | I | I | privileged or conflicting access goes BO, SEC, MGT; leaver removal has a same-day clock |
| Change and release | C | C | A | R | I | I | I | standard, normal, emergency; high risk to MGT; emergency reviewed within the week |
| Incident response | C | A | R | R | C | I | I | commander, operations, communications, planning; MGT at a declared major incident |
| Vulnerability management | C | A | R | R | C | I | I | missed SLA goes BO then MGT; delays are exceptions |
| Vendor and third-party risk | R | R | C | C | A | I | I | high-risk vendor decided by MGT before contract |
| Backup and DR test | C | C | A | C | I | I | I | a failed restore is an incident |
| Audit and evidence | C | R | R | R | A | R | I | findings go to the governing body, not only the audited team |
| Onboarding of a new service | A | C | R | R | C | I | I | the go-live gate fails closed |
| Exception and risk acceptance | A | C | C | C | R | I | I | owner, compensating control, expiry; MGT accepts high risk |

4. **Four different parties for real risk (INFERRED).** Request, approve, implement and verify are separated for anything with real risk. Change authority decides by risk tier: standard changes pre-approved with a tested runbook, normal changes peer-approved, emergency changes reviewed after, high-risk changes with a second approver.
5. **Incident roles (SRE book, VERIFIED).** Incident Commander holds state and assigns work; Operations Lead is the only group modifying the system during an incident; Communications Lead is the public face; Planning Lead tracks bugs, handoffs and divergence. Hand the commander role over by saying so aloud. Here the commander is the human and agents advise.
6. **On-call limits (SRE book, VERIFIED).** At least 50% of an SRE's time goes to engineering, so operational work is capped at 50% and on-call at 25%; at most 2 incidents per 12-hour shift; a single-site rota needs at least eight engineers, a dual-site team at least six per site. Below that, use business-hours coverage plus a managed service or a documented best-effort window (INFERRED).
7. **Cadences (INFERRED; the owner sets the periods).** Daily: alert and job review, request queue. Weekly: change review, vulnerability triage, on-call handoff. Monthly: risk register review, privileged access review, metrics review. Quarterly: access recertification, restore test, vendor review. After every incident: a blameless review with actions, owners and dates.
8. **Small team substitutes (INFERRED).** One person: automate the verifier (required checks, branch protection, plan before apply), log where the actor cannot edit, wait and re-read high-risk steps, and use a standing outside reviewer or a second model. Two to four: cross-review every change, one owner for access and exceptions, a managed detection service, an outside annual review. Five to ten: separate build and operate leads, a part-time security and GRC lead, a rotating change approver. Whatever the size, keep four things: separate admin from daily accounts, a tested restore, written exceptions with expiry, and a blameless review after every incident.
9. **Separation of duties for agent work (INFERRED; a proposal, not an adopted policy).** SD1 an agent never reviews or merges its own output. SD2 plan is not apply: planning roles never change a live system. SD3 an agent cannot approve a request it raised or widen its own grant. SD4 done is verified by someone other than the author. SD5 the challenger gets artifacts, not the author's conclusions, and never edits. SD6 an assessor does not operate or fix the control it tests. SD7 no role holds both repository write and live-system write. SD8 high-risk actions need a recorded reviewer verdict plus the human's approval. SD9 exceptions are time-boxed, owned by a human, and never accepted by an agent. SD10 analysts and assessors read copies of evidence and do not alter it. SD11 same-model, fresh-context review is weaker than a human or a different model; security-sensitive work gets a second review on the cyber tier. SD12 only the lead dispatches, and no role dispatches its own reviewer. SD13 where no independent dispatch exists, write SELF-REVIEW and list what was not verified; never claim independence.
10. **Modes and the roles that carry them.** A mode is how work gets done. Researcher: `researcher`. Analyst: `analyst`. Architect: no role yet; design review uses the `threat-model` pack. Engineer: `implementer` (and `transcriber` at the cheap tier). Operator: `cloud-engineer` or `devops-engineer`. Reviewer: `reviewer`. Challenger: `adversarial-reviewer`. Assessor: `assessor`. Planner: `planner`. Communicator: no role yet. Domain knowledge stays in packs, not in more roles.
11. **Metrics are read in pairs by someone else (INFERRED).** A function owns the metric it can move, a different function reviews it, and no metric is a target on its own. Examples: alert-to-incident ratio near 1:1 for detection, change lead time with change fail rate for delivery, control test pass rate with exceptions past expiry for GRC.

## Limits

- A design vocabulary, not an audit and not legal advice; the RACI is a starting table the owner changes.
- Layer names and the SoD list are not published standards. Do not cite them as one.
- Do not state what a framework requires unless its page was read and is under Sources.

## How to verify (before you hand it back)

- Each RACI row used has exactly one A: count the A cells per row; the count must be 1.
- Every rule you rely on carries VERIFIED (URL and date) or INFERRED in your deliverable.
- No decision is shown with the same party on both sides: list each approval and its approver, and confirm none is an agent.
- Pilot measurement (a prediction until run, thresholds are starting points): three planning tasks per run, pack on versus off, scored by code: each process names one accountable owner, one approver of record who is human, and the small-team substitute chosen. Measurement target: pack-on passes at least 20 percentage points more rubric items than pack-off; count reports where the author equals the verifier (target 0). Report counts, not significance; a pack with no gain after two runs is rewritten once, then retired.

## Deliverable

1. The process or decision in scope, with the RACI row (or a new one) and its single accountable owner.
2. The independence check: who requests, approves, implements and verifies.
3. The small-team substitute used, and what it does not replace.
4. VERIFIED or INFERRED on each rule used, and NOT COVERED.

## Sources

Read 2026-11-25 on the publisher's pages.

- VERIFIED: Google SRE book, "Managing Incidents": roles Incident Commander, Operations Lead, Communications Lead and Planning Lead; "The operations team should be the only group modifying the system during an incident" (https://sre.google/sre-book/managing-incidents/, read 2026-11-25).
- VERIFIED: Google SRE book, "Being On-Call": operational work capped at 50%, on-call at most 25%, at most 2 incidents per 12-hour shift, a single-site rota minimum of eight engineers and dual-site minimum of six (https://sre.google/sre-book/being-on-call/, read 2026-11-25).
- VERIFIED: NIST CSF 2.0 added the Govern Function to emphasize cybersecurity governance (https://www.nist.gov/cyberframework/faqs, read 2026-11-25).
- UNVERIFIED: the IIA Three Lines Model; the publisher page gave no role text on 2026-11-25 and only search summaries were read, so no wording is quoted.
- UNVERIFIED: ISO 27001 and ITIL 4 text on separating conflicting duties and on change types were not read on a publisher page on 2026-11-25; the change tiers above are common practice.
- INFERRED: the RACI, cadences, small-team table and SD1 to SD13 are this repository's design.
