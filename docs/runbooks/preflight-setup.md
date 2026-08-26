# A5b-preflight setup

The steps that cannot be automated, and the ones that can, in the order
they have to happen. Nothing here touches GitHub enforcement state.

## Human steps

Three things need a person, because GitHub and the two SaaS accounts have no
API for them.

### 1. Generate the two App private keys

<https://github.com/settings/apps/physshell-review-governor#private-key>

Press **Generate a private key** twice. Two `.pem` files download. Rename and
place them on the **primary**:

```text
~/.config/review-governor/K_primary.pem
~/.config/review-governor/K_edge.pem
chmod 600 on both
```

Both files stay on the primary only long enough to be verified and
delivered; `K_edge.pem` is then installed onto the edge over stdin, so it
never exists as a separate file in a world-readable place, and the local
copy is removed afterwards.

**Do not delete `K0` yet.** Deleting the shared key before both new paths
are proven turns a rotation into an outage.

### 2. Telegram bot

1. Talk to [@BotFather](https://t.me/BotFather), `/newbot`, keep the token.
2. Send any message to your new bot, so it has a chat to reply into.
3. Write the token to the primary:

```text
~/.config/review-governor/alerting.json
{"channel": "telegram", "bot_token": "<token>", "chat_id": "<filled in below>"}
```

`chat_id` is discovered from `getUpdates` once you have messaged the bot;
that step is scripted.

### 3. HetrixTools uptime monitor

<https://hetrixtools.com> · free tier, 1-minute interval.

```text
type      HTTP(s)
target    https://192-248-184-141.sslip.io/healthz
interval  1 minute
alert     after 2 consecutive failures
contact   the same Telegram bot, or Telegram personal
```

One minute is the reason for this provider specifically: the widely used
free tiers elsewhere are 5-minute, which fails the `<= 60 s` requirement
rather than meeting it quietly.

## Scripted steps

```text
key_verify.py --pem <path>            prove a key, from the host that uses it
key_verify.py --pem K0 --expect-rejected   prove the revoked key is refused
sentinel.py --once                    one sweep of the primary-side checks
alert_drill.py                        synthetic incident + recovery, end to end
```

## What runs, and where

```text
edge     governor-edge.service       Restart=always
         governor-watchdog.service   Restart=always, --window 0
                                     --branches main --context ai/final-review
primary  governor-heartbeat.service  15 s
         governor-signals.service    fast path, --window 0
         governor-reconcile.service  sweep, 30 s, writes the health file
         governor-sentinel.service   reads the health file, pages a human
```

The sentinel deliberately does not perform reconciliation. A loop that
alarms about its own staleness stops alarming at the moment it matters, so
the health file has one writer and a different reader.

Primary units are user units and need lingering, or they die with the
login session:

```text
loginctl enable-linger $USER
systemctl --user daemon-reload
systemctl --user enable --now governor-{heartbeat,signals,reconcile,sentinel}
```

They start Python through `nix develop`, not a pinned `/nix/store` path: a
pinned interpreter is one garbage collection away from a service that will
not start.

## Alert causes, and who raises them

```text
edge watchdog   heartbeat_age_critical / _warning
                watchdog_incident
                watchdog_revocation_outcome_unknown / _failed
                installation_token_mint_failed
                delivery_stuck
primary sentinel  reconciliation_stale
                  watchdog_not_polling
                  installation_token_mint_failed
                  auth_lost / refresh_outcome_unknown  (forwarded only)
primary fast path webhook_receiver_unavailable
```

Two of these have caveats worth knowing before you trust a green screen.

`auth_lost` and `refresh_outcome_unknown` are **forwarded**, not detected.
The sentinel never probes a refresh token, because Device Flow refresh
tokens are single-use with rotation and probing one can strand the
credential. Until a refresh path writes `auth-state.json`, the sentinel
reports `NOT_REPORTED` — which is not the same as healthy, and is rendered
differently on purpose.

`delivery_stuck` can only clear because the primary acknowledges progress
through `POST /signals/ack`. That acknowledgement is advisory: the primary's
own cursor remains the authority, reconciliation ignores both, and `DROPPED`
rows are never touched.

## Order of operations for the key rotation

```text
1  deploy K_primary and K_edge
2  prove both, independently, from their own hosts
3  point both runtimes at their own key, restart, prove again by fingerprint
4  only now: delete K0 in the App settings
5  prove K0 is rejected and both new keys still work
```

Step 3 before step 4 is the whole point. A runtime still holding `K0` in
memory will keep working until it restarts, which makes step 4 look
successful and step 5 fail hours later for reasons nobody remembers.
