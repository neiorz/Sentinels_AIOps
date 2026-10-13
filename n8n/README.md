# Sentinelas × n8n — Anomaly Alert Routing

This directory holds the n8n workflow that routes Sentinelas anomaly alerts to
**Telegram** and **Email**.

The platform runs against the shared **n8n Cloud** instance, so a teammate who
clones the repo gets working alerts with no local n8n at all. A self-hosted
setup is still documented at the bottom for development.

---

## Quick start (teammates)

```bash
git pull                      # brings .mcp.json with it
cp backend/.env.example backend/.env   # optional — every value is already the default
cd backend && python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

That's it. `N8N_WEBHOOK_URL` defaults to the cloud webhook in
`backend/app/config.py`, so anomalies detected by the ML pipeline are posted to
n8n Cloud without any configuration.

---

## Architecture

```
Sentinelas Backend
  ML Pipeline (IsolationForest + LightGBM)
        │ anomaly confirmed
        ▼
  POST /api/v1/webhooks/anomaly/trigger   ←── telemetry rows in
  POST /api/v1/chaos/inject               ←── dashboard "Inject Fault" button
        │ background task
        ▼
  n8n Webhook Trigger  (sentinelas-anomaly)   ←  n8n CLOUD
        │
        ▼
  Format Alert Message  (Code: validate payload, severity bands, render 3 bodies)
        │
        ▼
  Confidence ≥ 0.6 ?
        │
        ├─ false ──► Respond 200 Suppressed
        │
        └─ true ───► Telegram Alert  (parse_mode: HTML, retry ×3)
                          │
                          ▼
                     Gmail Alert     (OAuth2, HTML card)
                          │
                          ▼
                     Slack Alert     (mrkdwn, retry ×3)
                          │
                          ▼
                     Respond 200 OK  (per-channel delivery status)
```

**Why serial?** `Telegram → Gmail → Slack → Respond 200 OK` is a single chain.
Fanning the senders into the same `Respond 200 OK` node makes n8n fire the
response once per branch (`"Response already sent"` error).

Each sender sets `onError: continueRegularOutput`, so a channel that is missing
a credential or blips on the network is reported as `"slack_sent": false` in the
response instead of killing the alert — the other channels still deliver.

**Why 0.6 and not 0.7?** The LightGBM model emits ≈0.67 for clear anomalies, so
a 0.7 gate suppressed genuine detections.

> ### ⚠️ The threshold is decided in the Code node, not by the IF node
>
> `Confidence ≥ 0.6 ?` does **not** compare numbers. It tests a precomputed
> boolean: `{ type: 'boolean', operation: 'true' }` on `$json.above_threshold`,
> which **Format Alert Message** sets with plain JavaScript.
>
> This is deliberate. The obvious version — `IF` with
> `{ type: 'number', operation: 'largerEqual' }` and `rightValue: 0.6` — silently
> evaluated `0.95 >= 0.6` as **false** in n8n 2.x, routing every alert to
> *Suppressed* with no error anywhere. It was confirmed by feeding the IF node a
> literal `=0.95` constant: still false. `string/equals` on the same node worked
> fine, so only the numeric comparison path was broken.
>
> If you rewrite the gate, **verify it end to end with both a high and a low
confidence score** — a misconfigured condition looks exactly like "the alert
> didn't send".

### What the v2 workflow adds

| Change | Why |
|--------|-----|
| **Gmail node replaces SMTP** | OAuth2 — no App Password, which Google kept rejecting |
| **Slack as a third sink** | Telegram, Gmail **and** Slack all fire from one alert |
| **Payload validation** | Malformed bodies are reported in `payload_issues` instead of silently alerting as "unknown" |
| **Severity bands** | ≥0.90 `CRITICAL`, ≥0.75 `HIGH`, else `MEDIUM` — drives the alert colour, the emoji and the email subject prefix |
| **Per-channel message format** | Telegram gets HTML, Slack gets mrkdwn, email gets an HTML card. Each platform renders its own syntax — HTML pasted into Slack shows literal `<b>` tags |
| **Retry ×3 on Telegram and Slack** | Transient network blips no longer drop an alert |
| **Honest delivery status** | `telegram_sent`/`gmail_sent`/`slack_sent` read `result.message_id`/`id`/`ts`. Note Telegram nests its payload — `{ok, result: {message_id}}` — so reading `message_id` at the top level reports `false` even when the message was delivered |

### The three message formats

One payload, three renderings, produced by the **Format Alert Message** node:

```json
{
  "telegram_html": "...",   // parse_mode: HTML  — <b>bold</b>
  "slack_text":    "...",   // mrkdwn            — *bold*, `code`, :red_circle:
  "email_html":    "...",   // full HTML card with a per-severity accent colour
  "email_subject": "..."
}
```

The severity band picks the accent for all three — 🔴 `:red_circle:` `#b91c1c`
for CRITICAL, 🟠 `:large_orange_circle:` `#c2410c` for HIGH, 🟡
`:large_yellow_circle:` `#a16207` for MEDIUM.

---

## n8n Cloud

| Item | Value |
|------|-------|
| Instance | https://nourzz.app.n8n.cloud |
| Workflow | [Sentinelas — Anomaly Alert Router](https://nourzz.app.n8n.cloud/workflow/JAYllzvJ9FpsyrdB) |
| Workflow id | `JAYllzvJ9FpsyrdB` |
| Webhook URL | `https://nourzz.app.n8n.cloud/webhook/sentinelas-anomaly` |
| MCP endpoint | `https://nourzz.app.n8n.cloud/mcp-server/http` |

> Cloud webhook paths only answer once the workflow is **published**. An
> unpublished workflow returns `404` on `/webhook/…`.
>
> **Editing a published workflow does not change what the webhook runs.** An edit
> goes to a draft (`versionId`); the live route keeps serving `activeVersionId`
> until you **Publish** again. Symptom: you fix something in the UI, the code
> looks right, and the webhook behaves exactly as before. Check
> `activeVersionId` moves — it is the only proof the change went live.

### Credentials

MCP can read and edit workflows but deliberately **cannot create credentials** —
secrets never transit the MCP token. Two of the three channels need one:

**Telegram — blocks publishing:**

```
Node "Telegram Alert": Missing required credential: telegramApi
```

**Credentials → Add credential → Telegram (Legacy)** → paste the BotFather
access token → attach it to the **Telegram Alert** node.

**Slack — blocks publishing:**

```
Node "Slack Alert": Missing required credential: slackApi
```

**Credentials → Add credential → Slack API** → paste a **Bot User OAuth Token**
(`xoxb-…`) → attach it to the **Slack Alert** node. The bot posts to
`#sentinelas-alerts`, so that channel has to exist and the bot has to be in it:

1. https://api.slack.com/apps → **Create New App** → *From scratch*
2. **OAuth & Permissions → Bot Token Scopes** → add `chat:write`,
   `channels:read`
3. Install the app to your workspace → copy the **Bot User OAuth Token**
4. In Slack: `/invite @<your-bot>` in `#sentinelas-alerts`

> To post somewhere else, change `channelId` on the Slack node — or the
> `SLACK_CHANNEL` constant at the top of the **Format Alert Message** code, which
> feeds it.

Once both credentials are attached, **Publish**. Gmail needs nothing — the
instance's `Gmail account` credential (`gmailOAuth2`) was auto-assigned to the
**Gmail Alert** node on creation, and it sends *from* that Google account via
OAuth2, so there is no App Password and no "from" setting to misconfigure.

<details>
<summary>If you ever need to recreate the Gmail OAuth2 credential</summary>

1. https://console.cloud.google.com → create project `sentinelas`
2. **APIs & Services → Library** → enable **Gmail API**
3. **OAuth consent screen** → External → add your Google account as a **Test user**
4. **Credentials → Create credentials → OAuth client ID → Web application**
5. Authorised redirect URI: `http://localhost:5678/rest/oauth2-credential/callback`
6. Copy the **Client ID** and **Client Secret**
7. n8n **Credentials → Add credential → Gmail OAuth2 API** → paste both →
   **Sign in with Google**

</details>

---

## MCP access (`.mcp.json` at the repo root)

The repo ships an MCP server config so any MCP-aware client can drive the cloud
instance (list workflows, create, publish, inspect executions):

```json
{
  "mcpServers": {
    "n8n-mcp": {
      "type": "http",
      "url": "https://nourzz.app.n8n.cloud/mcp-server/http",
      "headers": { "Authorization": "Bearer <token>" }
    }
  }
}
```

54 tools are exposed, including `search_workflows`, `validate_workflow`,
`create_workflow_from_code`, `publish_workflow` and `list_credentials`.

> **Token handling:** this file deliberately contains a live owner-scoped token
> because the repository is private and the team wants clone-and-go access. If
> the repo is ever made public, transferred, or a member leaves, rotate it from
> the n8n Cloud **MCP settings** page and commit the replacement. It is a
> credential with full control of the instance — treat it like the database
> password, not like a URL.

---

## Payload schema

Every POST from Sentinelas to n8n carries this JSON body:

| Field                  | Type    | Description                                      |
|------------------------|---------|--------------------------------------------------|
| `service_name`         | string  | Originating microservice (e.g. `cart-service`)   |
| `anomaly_type`         | string  | Fault classification (e.g. `cpu_throttling@cart-service`) |
| `confidence_score`     | float   | Anomaly probability in [0, 1]                    |
| `timestamp`            | float   | Unix epoch of the detection event                |
| `status`               | string  | Alert lifecycle — `ANOMALY_DETECTED` by default  |
| `anomaly_score`        | float   | Raw IsolationForest decision score (negative = anomaly) |
| `root_cause_service`   | string  | ML-predicted root-cause service                  |
| `root_cause_fault_type`| string  | ML-predicted fault class                         |

Example:
```json
{
  "service_name": "cart-service",
  "anomaly_type": "cpu_throttling@cart-service",
  "confidence_score": 0.87,
  "timestamp": 1718000000.0,
  "status": "ANOMALY_DETECTED",
  "anomaly_score": -0.1234,
  "root_cause_service": "cart-service",
  "root_cause_fault_type": "cpu_throttling"
}
```

`notify_n8n()` forwards the three RCA fields when present, so the alert can name
a real root cause instead of falling back to "Investigating…".

---

## API endpoints

### `POST /api/v1/webhooks/anomaly/trigger`

Runs the full ML pipeline on a telemetry snapshot and fires the n8n webhook
**only when an anomaly is confirmed**.

```bash
curl -s -X POST http://localhost:8000/api/v1/webhooks/anomaly/trigger \
  -H "Content-Type: application/json" \
  -d '{"rows":[{"service_name":"cart-service","cpu_percent":95.0,
       "memory_mb":1800.0,"latency_ms":2400.0,"error_rate":0.45,"service_up":1}]}'
```

### `POST /api/v1/webhooks/anomaly/notify`

Bypasses the ML pipeline — forwards a pre-formed payload directly to n8n.

### `POST /api/v1/chaos/inject`

The dashboard's **"Inject Fault & Trigger Alert"** button. Injects a real fault,
runs the AI investigation, then dispatches the resulting root cause and
confidence to n8n. Expect ~20–30s: the alert waits for the investigation.

```bash
curl -s -X POST http://localhost:8000/api/v1/chaos/inject \
  -H "Content-Type: application/json" \
  -d '{"service":"payment-service","experiment_type":"PodFailure","tenant_id":"tenant_a","duration_seconds":60}'
```

---

## Telegram message format

`parse_mode` is **HTML**, not Markdown. Markdown (`_`) breaks on fault labels
like `cpu_throttling` and Telegram returns
`can't parse entities: Can't find end of the entity`.

```
🚨 Sentinelas Anomaly Alert

Service: cart-service
Type: cpu_throttling@cart-service
Confidence: 91.0%
Status: ANOMALY_DETECTED
Time: 2026-10-12T17:10:24.000Z
Root Cause: cart-service — cpu_throttling
```

---

## Running your own n8n (optional)

Useful when developing offline or when you want a private instance.

| Item | Value |
|------|-------|
| Container | `n8n_main` (`n8nio/n8n:latest`, v2.39.10) |
| URL | `http://localhost:4555` (host `4555` → container `5678`) |
| Volume | `/home/nourz/n8n_backup` → `/home/node/.n8n` |
| Workflow id | `sentinelas-anomaly-wf1` |
| Import file | `n8n/sentinelas_n8n_workflow.json` |

Point the backend at it:

```
# backend/.env
N8N_WEBHOOK_URL=http://localhost:4555/webhook/sentinelas-anomaly
```

CLI changes need a restart before the webhook is registered:

```bash
docker stop n8n_main

docker run --rm -v /home/nourz/n8n_backup:/home/node/.n8n -v "$PWD/n8n:/input" \
  --entrypoint n8n n8nio/n8n:latest import:workflow --input=/input/sentinelas_n8n_workflow.json

docker run --rm -v /home/nourz/n8n_backup:/home/node/.n8n \
  --entrypoint n8n n8nio/n8n:latest update:workflow --id=sentinelas-anomaly-wf1 --active=true

docker start n8n_main
```

Credentials are stored **encrypted** in the n8n database and are never exported
into the workflow JSON:

| Credential | id | type |
|------------|----|------|
| Sentinelas Alert Bot | `sentinelas-telegram-bot` | `telegramApi` |
| SMTP | `sentinelas-gmail-smtp` | `smtp` |

Create one while the container is stopped:

```bash
cat > /tmp/cred.json <<'JSON'
[ { "id": "sentinelas-gmail-smtp", "name": "Gmail SMTP", "type": "smtp",
    "data": { "user": "you@example.com", "password": "app-password",
              "host": "smtp-relay.brevo.com", "port": 587, "secure": false,
              "starttls": true, "disableStartTls": false } } ]
JSON

docker stop n8n_main
docker run --rm -v /home/nourz/n8n_backup:/home/node/.n8n -v /tmp:/input \
  --entrypoint n8n n8nio/n8n:latest import:credentials --input=/input/cred.json
docker start n8n_main
```

A cloud-ready variant with credentials stripped (for importing into a fresh
instance) is `n8n/sentinelas_n8n_workflow.cloud.json`.

> **Why not `$vars`?** The `variables` table on the self-hosted instance is
> empty, so `{{ $vars.TELEGRAM_CHAT_ID }}` resolved to nothing. Chat ID and mail
> addresses are inline on the nodes; secrets stay in encrypted credentials.

---

## Extending the workflow

- **PagerDuty** — HTTP Request node against PagerDuty Events API v2.
- **Slack threads** — thread follow-up notes onto the alert message (`thread_ts`).
- **Jira** — auto-create an incident ticket with the Jira Software node.
- **Suppress low-severity** — an extra IF on `anomaly_type`.
- **Tune the gate** — the `Confidence ≥ 0.6 ?` node.
