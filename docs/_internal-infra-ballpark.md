# INTERNAL ONLY — do NOT send to vendor

Companion to `CLOUD-INFRA-ESTIMATION-BRIEF.md`. This holds our own budget estimate
and open decisions. Kept in a **separate file** so the vendor brief can be shared
as-is without leaking our number (which would anchor their quote against us).

## Our own budgetary ballpark (on-demand, USD/mo, excl. LLM)

- Tier A (hardened pilot): ~$300–450
- Tier B (org-wide): ~$600–900
- GPU option (only if E3 self-host): +$500–1,500 (medium) / +$2,000–3,500 (comfortable, on-demand)

## LLM as operating expense (E1/E2 path — separate from infra above)

- Managed Claude API is per-token opex, NOT in the vendor infra quote.
- Frontier tiers: ~$3–5 / 1M input, ~$15–25 / 1M output; prompt caching cuts
  repeated-prefix input cost up to ~90%.
- Very rough band at 200–500 active users: low-thousands USD/mo, swinging on model
  tier (Haiku << Sonnet << Opus) and caching. **Needs a measured-pilot number
  before quoting to management.**

## Open internal decisions that move the numbers

- **D1 data-residency ruling** → E1 (Claude via Bedrock, in-region) vs. E2 (external
  API) vs. E3 (self-host GPU). Largest single lever; E3 is the only one that adds
  fixed infra cost.
- **Confirm Claude tier availability in ap-south-1 (Mumbai) Bedrock** before committing
  to E1 in that region.
- Actual org-wide user/department count (swings Tier B node count).
- Region (Mumbai assumed).
- SSO provider: Entra ID vs. Keycloak.

## Pre-send checklist for the vendor brief

- [ ] Fill in the contact name/email in the header.
- [ ] Confirm or change the `<confirm>` assumptions (users, region, availability target, egress).
- [ ] Do NOT attach this file.
