# Edge host deployment (A5a-c1)

What has to exist on the dedicated VPS before `A5a-c1` can run. Written
before the host exists, so provisioning is a checklist rather than an
improvisation.

## What the edge is, and is not

```text
IS      webhook receiver · durable delivery spool · heartbeat sink
        independent watchdog (failure-only)
IS NOT  a source of policy truth · a place where a SUCCESS is decided
        a merge actor · a ruleset administrator
```

The primary keeps its authoritative decision store. The edge holds only
what GitHub delivered, when the primary was last alive, and what the
watchdog did about it.

## Provisioning checklist

1. **Host**: the smallest VPS that stays up. Its value is being a *different
   failure domain* from the primary, not being fast.
2. **DNS**: an `A`/`AAAA` record for `governor-edge.<your-domain>` pointing
   at it. If the domain sits behind Cloudflare, leave it **DNS only** — no
   proxy, no tunnel — unless you deliberately want a third party to see
   webhook payloads. A2a already recorded that trade-off once.
3. **TLS terminates on the VPS**: Caddy, nginx + certbot, or equivalent,
   reverse-proxying to `127.0.0.1:8931`. The edge service binds to
   localhost only and never speaks TLS itself.
4. **Runtime**: Python 3.11+ and the two harness modules
   (`edge_service.py`, `edge_watchdog.py`, `edge_store.py`). No third-party
   packages are required.
5. **Secrets**, in `~/.config/review-governor-edge/`, mode `0600`:

```text
webhook-secret      shared with the GitHub App webhook configuration
heartbeat-secret    shared with the primary's heartbeat client
app.pem             Governor App private key
app-public.json     app id + pem path (same shape as on the primary)
```

**Never on the edge**: the user OAuth access or refresh token, anything
granting `administration`, and any credential able to publish a passing
conclusion.

6. **Services** (systemd or equivalent):

```text
governor-edge.service      edge_service.py --port 8931
                           Restart=always
governor-watchdog.service  edge_watchdog.py watch --repo <owner>/<repo>
                                             --branches main
                                             --stale-after 45 --interval 10
                                             --window 0
                           Restart=always
```

`--window 0` and `Restart=always` are both load-bearing. A5a-c2 found the
watchdog deployed with a bounded window and `Restart=on-failure`: it exited
`0` after its first revocation, systemd correctly did not restart it, and
the independent failure domain switched itself off the first time it was
needed — while the service history still read as a successful run. Never
deploy this unit with `--stop-after-incident`; that flag exists for bounded
fixtures.

7. **On the primary**, two supervised services:

```text
heartbeat_client.py --endpoint https://governor-edge.<your-domain>
                    --interval 15
signal_client.py    --endpoint https://governor-edge.<your-domain>
                    --repo <owner>/<repo> --interval 2 --window 0
```

A heartbeat that stops is the signal; there is nothing else to configure.
`signal_client` is the healthy-path detector — it pulls delivery *metadata*
from the edge and re-reads GitHub for every fact. It is an optimisation, not
a dependency: reconciliation keeps reading GitHub on its own schedule and
never consults the cursor or the spool, so an edge that goes silent costs
latency and nothing else.

8. **GitHub App webhook**: set the URL to
   `https://governor-edge.<your-domain>/github/webhook`, set the secret to
   the value already in `webhook-secret` (via `PATCH /app/hook/config` with
   an App JWT, so it never passes through a human's clipboard — A2a
   established that path), and subscribe to `pull_request`. The
   `github_app_authorization` event arrives regardless.

## Health

```text
GET https://governor-edge.<your-domain>/healthz
    -> {"ok": true, "last_primary_heartbeat": "..."}
```

Alert on: heartbeat age above 45 s, webhook deliveries stuck in `RECEIVED`,
and any `watchdog_incidents` row that is still open.

## Degradation modes

```text
WEBHOOK_HEALTHY   normal; reconciliation every <= 30 s
WEBHOOK_DOWN      polling-only degradation; the gate stays active; detection
                  SLO degrades to the poll interval; visible in health state
PRIMARY_DOWN      the watchdog revokes standing successes
BOTH_DOWN         the watchdog still revokes standing successes
```

A webhook outage does **not** open the gate and does **not** justify
break-glass. It makes detection slower, which is a different thing from
making it permissive.

## Blast radius, stated rather than implied

To patch its own App's check runs, the edge needs App installation
authority. On a compromised edge host, `WatchdogCapability` is a program
boundary, not a cryptographic sandbox: an attacker with code execution
there could use the same key differently. This is accepted for now because
the alternatives — user OAuth or `administration` on that host — are
strictly worse, and because the watchdog's key cannot merge, cannot change
rules, and cannot publish a passing state through any code path that
exists.

Consequences to keep in view: rotate the App key if the edge is ever
suspected; keep the edge's package surface minimal; and remember that the
primary, not the edge, is what an attacker would need to compromise to
manufacture a *success*.

## A5a-c1 qualification, once the host is up

```text
1  edge healthy, public endpoint verified
2  real signed GitHub delivery: HMAC PASS, durable-before-ACK PASS
3  primary heartbeat healthy
4  standing confirmed probe success
5  kill primary HOST/process connectivity — not merely one Python loop
6  edge watchdog independently detects > 45 s
7  GET exact run -> success->failure -> independent GET -> CONFIRMED
8  merge attempt -> BLOCKED
9  primary restored -> old success NOT restored
10 drop one webhook delivery from processing on purpose
11 reconciliation finds the corresponding GitHub state within
   <= 30 s + processing budget
```

All of it runs against an isolated probe ref and a probe context.
`ai/final-review` stays unused until A5b.
