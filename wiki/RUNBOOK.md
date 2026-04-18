# Runbook

Incidents you will see, in rough frequency order, and how to unblock them.

## 0. Always-first checks

```bash
# Is the service up?
curl -fsS https://api-workspace.raavasolutions.com/health
curl -fsS https://api-workspace.raavasolutions.com/ready

# App Runner status
ARN="$(terraform -chdir=infra/terraform output -raw apprunner_service_arn)"
aws apprunner describe-service --service-arn "$ARN" --region us-east-1 \
  --query 'Service.[Status,ServiceUrl,CreatedAt]' --output table

# Last 15 minutes of application logs
aws logs tail /aws/apprunner/monolith-workspace-api/application \
  --since 15m --region us-east-1 --follow
```

If `/ready` is red but `/health` is green, the container is up but
Postgres is unreachable. Jump to "DB unreachable" below.

---

## 1. SSE stream disconnects silently

**Symptoms:** bridges log "SSE closed after Ns" at a regular interval;
dashboard live-updates stop.

**Likely causes and fixes:**

- **Missing heartbeat.** Confirm `SSE_HEARTBEAT_SECONDS` is set and
  positive (default `15`). `app/services/realtime.py` emits a
  `: heartbeat\n\n` comment line every N seconds to keep the TCP
  connection warm. If the env var is `0` or unset, proxies with idle
  timeouts will close the connection.
- **Cloudflare proxying enabled.** Custom domain must be `DNS only`,
  not proxied. Cloudflare proxies buffer SSE by default. Fix in
  Cloudflare dashboard: toggle the grey-cloud for `api-workspace`.
- **App Runner instance cycled.** Autoscaling or a deploy replaced the
  instance; clients should reconnect automatically. If they do not,
  inspect bridge reconnect logic.
- **Wrong `agent_id` for the token.** The stream returns 403 with
  `"agent_id does not match bearer token"`. Double-check that the
  bridge uses its own agent's ID.

```bash
# Replay what the client should be seeing, using a real token:
curl -N \
  -H "Authorization: Bearer sk_machine_..." \
  "https://api-workspace.raavasolutions.com/api/workspace/stream?agent_id=<uuid>&workspace_id=<uuid>&channels=<uuid>"
```

---

## 2. "Invalid machine token" / 401 for an agent

**Symptoms:** new or reprovisioned agent container can't post messages.
Logs show `workspace.auth` with "Invalid machine token".

**Checks:**

1. Confirm token format: `sk_machine_` + exactly 64 hex chars. If it
   doesn't match the regex in `app/auth.py`, the service tries to
   parse it as a Clerk JWT and produces a different error.
2. Fleet API is the minter — confirm the row exists in
   `agent_machine_tokens`:
   ```sql
   SELECT agent_container_id, workspace_id, tenant_id, revoked_at
   FROM agent_machine_tokens
   WHERE token_hash = encode(digest('<raw-token>', 'sha256'), 'hex');
   ```
3. `revoked_at IS NOT NULL` → Fleet revoked the credential (container
   was torn down). Reprovision the agent via Fleet to mint a fresh
   token.
4. Token hash not present → token was never persisted here. Fleet API
   write failed, or the bridge is using a token from a different
   environment.

---

## 3. Clerk JWT not validating

**Symptoms:** human requests return 401 with "Malformed JWT" or 501
"Production Clerk JWKS verification not wired yet".

**Checks:**

- `VERIFY_CLERK=false` in dev accepts any JWT envelope. If you see 401
  "Malformed JWT" here, the Authorization header is wrong shape
  (missing `Bearer `, truncated token, etc.).
- `VERIFY_CLERK=true` in prod returns 501 until the JWKS verification
  path is finished. Track this via the `contract-gap` TODO in
  `app/auth.py::_resolve_clerk_token`. Until then, production either
  runs with `VERIFY_CLERK=false` (acceptable for locked-down operator
  dashboards) or keeps a stand-in at the edge.

---

## 4. Message sent but never fans out over SSE

**Symptoms:** `POST /channels/{id}/messages` returns 201 but subscribers
do not receive the event.

**Checks:**

1. The subscriber must be connected to the right topics. Topics are
   `channel:<channel_id>` and `agent:<agent_container_id>`. Confirm the
   client's `?channels=` query param contains the channel_id.
2. SSE fanout is in-process per App Runner instance. If the writer
   instance and reader instance are different, the event is lost.
   Mitigations:
   - Bridges call `GET /api/workspace/inbox` on reconnect to backfill.
   - Dashboard's channel view polls `GET /channels/{id}/messages` every
     N seconds as a belt-and-braces backup.
   See DECISIONS.md ADR on in-process event bus — when this stops being
   acceptable, graduate to Redis pub/sub or Supabase Realtime.
3. The sender may have been rejected by idempotency. Check logs for a
   unique-violation warning on `(channel_id, idempotency_key)`.

```bash
# Inspect the message row directly
psql "$DATABASE_URL" -c \
  "SELECT id, created_at, sender_human_id, sender_agent_container_id,
          left(content, 120), idempotency_key
   FROM messages
   WHERE channel_id = '<uuid>'
   ORDER BY created_at DESC
   LIMIT 5;"
```

---

## 5. DB unreachable

**Symptoms:** `/ready` returns `{"status":"not_ready"}`; 500s on any
DB-touching route.

**Checks:**

1. Supabase status page — `https://status.supabase.com/`.
2. `DATABASE_URL` rotated? Trigger a redeploy to pick up the new
   Secrets Manager value (secrets are only read at cold start).
3. Connection pool exhausted — `asyncpg.create_pool(min=2, max=10)`
   per instance, 10 instances max. Upper bound is 100 connections
   workspace-wide; Supabase transaction pooler handles >100 by
   multiplexing.
4. NAT / egress: App Runner has public egress by default (no VPC
   connector). If a future change adds one, make sure Supabase is
   reachable from the attached subnets.

---

## 6. 5xx spike after a deploy

**Playbook:**

1. Quick check whether it's truly deploy-related:
   ```bash
   aws apprunner list-operations --service-arn "$ARN" --region us-east-1 \
     --query 'OperationSummaryList[:5].[Type,Status,StartedAt,EndedAt]'
   ```
2. If the most recent deployment just finished and 5xx started at the
   same timestamp — roll back per `OPERATIONS.md` "Rollback".
3. Look for a loud exception:
   ```bash
   aws logs tail /aws/apprunner/monolith-workspace-api/application \
     --since 15m --filter-pattern "ERROR" --region us-east-1
   ```

---

## 7. CI deploy failed

- `test` job red → fix the test and re-push.
- `build-and-push` red but `test` green → commonly an expired/rotated
  OIDC trust or a missing `AWS_DEPLOY_ROLE_ARN` secret. Compare the
  role ARN in Terraform outputs with the GitHub repo secret.
- `deploy` red → `aws apprunner start-deployment` failed. Inspect the
  operation:
  ```bash
  aws apprunner list-operations --service-arn "$ARN" --region us-east-1
  aws apprunner describe-service --service-arn "$ARN" --region us-east-1
  ```

---

## 8. Custom domain cert pending forever

- Validation CNAMEs must be **DNS only** in Cloudflare, not proxied.
- If validation has been pending > 1 hour, re-run
  `terraform output custom_domain_validation_records` and confirm each
  record exists in Cloudflare with exactly matching name and value.
- As a last resort, delete and recreate the custom domain association:
  ```bash
  terraform apply -var="enable_custom_domain=false"
  # wait a minute
  terraform apply -var="enable_custom_domain=true"
  ```
