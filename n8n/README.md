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
        ├─ confidence ≥ 0.6 ──► Format Message  (HTML + plain text bodies)
        │                             │
        │                             ▼
        │                       Telegram Alert  (parse_mode: HTML)
        │                             │
        │                             ▼
        │                       Email Alert     (SMTP)
        │                             │
        │                             └─► Respond 200 OK
        │
        └─ confidence < 0.6 ──► Respond 200 Skipped
```

**Why serial?** `Telegram Alert → Email Alert → Respond 200 OK` is a single
chain. Fanning *both* senders into the same `Respond 200 OK` node makes n8n fire
the response twice (`"Response already sent"` error).

**Why 0.6 and not 0.7?** The LightGBM model emits ≈0.67 for clear anomalies, so
a 0.7 gate suppressed genuine detections.

---

## n8n Cloud

| Item | Value |
|------|-------|
| Instance | https://nourzz.app.n8n.cloud |
| Workflow | [Sentinelas — Anomaly Alert Router](https://nourzz.app.n8n.cloud/workflow/d1QiZbMLmR7fOJDA) |
| Workflow id | `d1QiZbMLmR7fOJDA` |
| Webhook URL | `https://nourzz.app.n8n.cloud/webhook/sentinelas-anomaly` |
| MCP endpoint | `https://nourzz.app.n8n.cloud/mcp-server/http` |

> Cloud webhook paths only answer once the workflow is **published**. An
> unpublished workflow returns `404` on `/webhook/…`.

### Publishing checklist

Publishing is blocked until both nodes have credentials attached:

```
Node "Telegram Alert": Missing required credential: telegramApi
Node "Email Alert":    Missing required credential: smtp
```

Add them in the cloud UI (**Credentials → Add credential**), then attach each to
its node and hit **Publish**. MCP can read and edit workflows but cannot create
credentials, so this step is manual by design — secrets never transit the MCP
token.

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
- **Slack** — built-in Slack node posting to `#incidents`.
- **Jira** — auto-create an incident ticket with the Jira Software node.
- **Suppress low-severity** — an extra IF on `anomaly_type`.
- **Tune the gate** — the `Confidence ≥ 0.6 ?` node.
