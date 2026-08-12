# ChargeZone Jira — API access request

**Audience:** the Jira administrator at ChargeZone.
**Purpose:** provision read-only REST API access to the **`QCH` (Quench)** project on
ChargeZone's Atlassian Cloud site, so Quench's NOC assistant can read Quench's own
tickets without anyone logging into the Jira UI.

Send this document (or the summary below) to the ChargeZone Jira admin. Everything in
**§1 What we need** is the actual ask; §2–§5 exist to answer the security questions the
request will raise.

---

## Summary (the short version to paste into an email)

> We'd like a **read-only API service account** on your Atlassian Cloud site
> (`team-1604552506221.atlassian.net`), scoped to the **QCH (Quench)** project only.
>
> Specifically:
> 1. Invite a user — ideally a dedicated service account we name below, or an existing
>    Quench user you already trust — and grant it **Jira product access**.
> 2. Grant that account **Browse Projects** on **QCH only**. No other project, no admin
>    rights, no create/edit/transition/delete permissions.
> 3. Confirm the exact site URL so we point at the right instance.
> 4. Tell us the account's email address once it's active — the account holder creates
>    their own API token; you don't need to (and can't) create one for them.
>
> We only issue HTTP `GET` requests; the client is read-only by construction and refuses
> any other method. Details, including the exact REST endpoints we call, are below.

---

## 1. What we need

| # | Item | Detail |
|---|---|---|
| 1 | **Site URL** | We currently target `https://team-1604552506221.atlassian.net`. Please confirm this is the correct/current ChargeZone Cloud site. |
| 2 | **An account** | A dedicated service account is strongly preferred over a human's personal login — see §1.1. |
| 3 | **Jira product access** | The account needs a Jira licence seat. Without product access the REST API returns `401`/`403` regardless of project permissions. |
| 4 | **`Browse Projects` on `QCH`** | This single permission is sufficient for everything we do. Grant it via a project role on QCH only. |
| 5 | **Field visibility** | The **"Priority Level"** custom field (`cf[10120]`, the real P1/P2/P3 used on QCH) must be visible to that account. It's the field our reporting is built on — Jira's built-in Priority is not what ChargeZone uses. |
| 6 | **Confirmation back to us** | The account's email address, and confirmation that the token has no expiry surprise (see §3). |

### 1.1 Preferred: a dedicated service account

Please create/invite:

```
Suggested address:  <to be filled in by Quench, e.g. noc-bot@quenchchargers.com>
Display name:       Quench NOC Assistant (read-only)
```

Why a service account rather than a person's login:

- **It survives staff changes.** A personal token dies when that person leaves or rotates
  their credentials, and the integration breaks with no obvious owner.
- **Its audit trail is unambiguous.** Every API read is attributable to a named integration
  rather than blended into a human's activity.
- **It can be revoked in isolation** without disturbing anyone's day-to-day access.

If a dedicated account isn't possible under ChargeZone's licensing, an existing Quench-side
user with QCH access works — we'd just need to know whose it is so we can plan around
their departure or password rotation.

### 1.2 What we explicitly do NOT need

Please do **not** grant any of the following. If your permission scheme bundles them, we'd
rather you carve out a narrower role:

- Any access to projects other than **QCH**
- Create / Edit / Delete Issues, Transition Issues, Add Comments, Manage Attachments
- Jira administrator or site administrator
- User management, billing, or org-level access
- Access to Confluence or any other Atlassian product on the site

---

## 2. How authentication works (and why we're asking for an email, not a password)

Atlassian Cloud's REST API authenticates over **HTTP Basic** using
`email : API token` — never the account password. Two consequences worth stating up front,
because they usually come up:

1. **You cannot create the API token for us.** In Atlassian Cloud, API tokens are generated
   by the account holder at
   <https://id.atlassian.com/manage-profile/security/api-tokens>. So the flow is: you
   provision and activate the account → whoever controls that mailbox logs in once and
   generates the token → that token is what our service uses.
2. **The token carries exactly the account's permissions — no more.** There is no way for a
   token to exceed what the account can already see in the UI. Locking down the account, as
   in §1, is therefore the whole of the security control.

If ChargeZone's policy requires **scoped** API tokens rather than classic unscoped ones,
the scopes we need are read-only:

```
read:jira-work      # issues, comments, projects, statuses, dashboards, filters
read:jira-user      # assignee / reporter display names
```

No `write:` or `manage:` scope is required for anything we do.

---

## 3. Token lifetime and rotation

Atlassian API tokens are created with an expiry date (up to one year). Please tell us the
expiry you set, so we can schedule rotation *before* it lapses rather than discovering it
through a production outage.

Rotation is a two-minute job on our side — a new token is dropped into our config and the
service is restarted. No change is required on ChargeZone's side, provided the account
itself stays active.

---

## 4. Exactly what we call (for your security review)

The integration is a small self-hosted service. Its Jira client
(`chargezone-jira-mcp-server/cz_jira_mcp/auth.py:29`) raises an exception on any method
other than `GET`/`HEAD`/`OPTIONS` — write access is impossible even if someone tried, and
it's covered by tests.

The project scope is likewise enforced in code, not by convention: every search has
`project = "QCH"` prepended server-side, and by-key lookups accept only `QCH-####` keys.
Sort parameters are validated against a strict whitelist so they can't be used to break out
of the project filter.

| REST endpoint (all `GET`) | What we read |
|---|---|
| `/rest/api/3/search/jql` | QCH issue search, always scoped to `project = "QCH"` |
| `/rest/api/3/issue/{key}` | One QCH issue's fields |
| `/rest/api/3/issue/{key}/comment` | Comments on a QCH issue |
| `/rest/api/3/project/QCH` | Project metadata |
| `/rest/api/3/project/QCH/statuses` | Workflow statuses and issue types |
| `/rest/api/3/priority` | Priority list (site-level reference data) |
| `/rest/api/3/dashboard`, `/rest/api/3/filter/search` | Dashboards / saved filters that reference QCH |

Typical usage is a handful of requests per question asked — this is an interactive
assistant, not a bulk sync or a mirror. We do not copy your Jira into a separate database.

---

## 5. Data handling

Worth stating plainly, because it's the question a security reviewer should ask:

- The tickets we read are **Quench's own field-operations data** on QCH — not ChargeZone's
  internal projects, which we neither request nor can reach.
- Ticket content is passed to a third-party LLM provider to answer the operator's question.
  Data-residency sign-off for this is tracked on the Quench side (decision **D1**); the
  integration is **internal/dev use only** until that's signed off.
- The API token is injected as a secret at runtime and is never committed to source control.

---

## 6. Verification (once access is granted)

We'll confirm end-to-end with a single read against the QCH project:

```bash
curl -s -u "$CZ_JIRA_EMAIL:$CZ_JIRA_API_TOKEN" \
  -H "Accept: application/json" \
  "https://team-1604552506221.atlassian.net/rest/api/3/search/jql?jql=project%3D%22QCH%22&maxResults=1"
```

Expected outcomes:

| Response | Meaning |
|---|---|
| `200` with an issue | Working — nothing further needed. |
| `401 Unauthorized` | Bad email/token pair, or the token was revoked. |
| `403 Forbidden` | Account authenticated but lacks **Jira product access**. |
| `400` / empty results with `"does not exist"` | Account lacks **Browse Projects** on QCH, or the project key differs. |

A `404` on the project endpoint means the same thing as `403` in Jira Cloud — it hides
existence from unauthorized callers — so treat it as a permissions problem, not a typo.

---

## 7. Checklist for the ChargeZone admin

- [ ] Confirm the Cloud site URL (`team-1604552506221.atlassian.net` or the current one)
- [ ] Create/invite the service account and activate it
- [ ] Grant **Jira product access** (licence seat)
- [ ] Grant **Browse Projects** on **QCH only**, via a project role
- [ ] Confirm the **"Priority Level"** field (`cf[10120]`) is visible to that account
- [ ] Confirm no other project, and no write/admin permissions, are attached
- [ ] Send back: the account email, the token expiry date, and any scoping requirements

---

## For Quench: what to do with the token when it arrives

Do **not** paste the token into chat, tickets, or commits. Put it straight into
`chat/.env`:

```
CZ_JIRA_BASE_URL=https://team-1604552506221.atlassian.net
CZ_JIRA_EMAIL=<the account email>
CZ_JIRA_API_TOKEN=<the token>
```

Then recreate the container — a plain `restart` will **not** pick up a changed `.env`:

```powershell
cd E:\cjain\noc-mcp-agent\chat
docker compose up -d --force-recreate chargezone-jira-mcp-server api
```

Restarting `api` alongside it is deliberate: LibreChat inspects each MCP server once at
startup, so it needs to re-handshake to see the reconnected tools. Confirm with:

```powershell
docker compose logs api | Select-String "\[MCP\]\[chargezone\]"
```

You should see the eight `cz_jira_*` tools listed. See `chat/RUNBOOK.md` for the rest of
the stack.
