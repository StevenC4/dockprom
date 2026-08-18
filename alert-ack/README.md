# alert-ack — posting alerts to #alerts, and marking them resolved

Two jobs, and it is worth keeping them apart because they have different failure consequences.

**1. It posts the alerts.** Alertmanager calls `POST /alertmanager`; this renders the message and
sends it with `chat.postMessage`, remembering the `ts` of every post it makes.

**2. It marks them resolved — automatically, and by hand.** When Alertmanager says the alert
cleared, every post this service made for it is rewritten green. Posts it did not author (see
below) get a ✅ and a threaded reply instead. The **Mark resolved** button is still there for
clearing a post yourself before the alert actually resolves.

```
                    ┌──► email  (always, unconditionally — the safety net)
Alertmanager ───────┤
   │                └──► POST /alertmanager ──► alert-ack ──chat.postMessage──► #alerts
   │                        (bearer auth)           │                              ▲
   │ alert clears                                   │ remembers channel+ts         │
   └────────────────────────────────────────────────┤                              │
                                    chat.update green ──────────────────────────────┘
                                    + ✅/thread on posts it could not author

  [Mark resolved] ──► Slack ──socket──► homelab-slack-gateway ──signed POST──► alert-ack
                                          (unicast, "alert:" prefix)              │
                            #alerts post rewritten green ◄──── response_url ◄──────┘
```

## The three things worth knowing

**A webhook-authored post can never be edited.** Slack refuses a bot token an edit to a message
written by an *incoming webhook* — `cant_update_message`, verified against a real post on
2026-07-23 and pinned in `tests/test_slack.py`. Every alert posted before 2026-08-18 was written
that way, so those can never go green; they get a ✅ reaction and a threaded "Resolved" reply,
which is the whole of what Slack permits on someone else's message. This is also the entire reason
job 1 exists: auto-resolve is impossible unless the post is ours.

**It IS in the alerting path now, and that reverses an earlier decision.** `alertmanager/config.yml`
used to reject putting a container between the alert and Slack, because one did take alerts down
for 9 days in July 2026. That reasoning was sound; Slack simply leaves no alternative. The
mitigation is not that this service is reliable — it is that **`email_configs` sits alongside the
webhook on every route**, so a dead alert-ack costs the Slack rendering and nothing else. The
July 2026 outage was alerts reaching *nobody*; the worst case now is alerts reaching you by email.
Alertmanager also counts `notifications_failed_total{integration="webhook"}`, which feeds the
`service="alerting"` route — so the bridge is watched by the same rule that watches every notifier.

**The button is an acknowledgement, not a state change.** Nothing is sent back to Alertmanager. If
the alert is still firing it will notify again on `repeat_interval` (24h) as a **new, red** post. A
green post from the *button* means *a human saw this one*; a green post from the *resolve path*
means Alertmanager says it stopped firing. The footer says which. To stop an alert, silence it.

## conversations.history is throttled, and lies about it

Finding the legacy posts needs `conversations.history`, which Slack rate-limits for
non-Marketplace apps to roughly one call a minute — and it does **not** answer 429. It answers
`ok: true` with an **empty** `messages` list. Measured on this workspace 2026-08-18: the same query
returned 166 messages, then 0, 0, 0, 0 at 15s intervals, then 166 again at t+90s.

Believed at face value, that reads as "the backlog is clean" when it means "I was not allowed to
look" — a silent false negative in the one feature meant to remove silent false negatives. So
`sweep.HistoryCache` retries an empty page, caches a good one for 5 minutes so a burst of resolves
costs one call, and logs at WARNING when it gives up. The sweep also runs on its **own worker**
(`run_sweep_worker`), because retrying with backoff would otherwise stall the posting of new
alerts behind it.

## State, and the uid that broke everything last time

The `ts` of every post lives in SQLite on the `alert_ack_data` volume. Losing it means a post that
can never be marked resolved, so unlike the click path — which is deliberately in-memory — this is
durable. The Dockerfile creates `/data` owned by 65534 *before* the `USER` switch so a fresh volume
inherits that ownership; `alertmanager/alert_ack_token` needs the same care (`chown 65534:65534`,
`chmod 0400`). A file the `nobody` uid could not read is exactly what silently broke every notify
for 9 days in July 2026, and `amtool check-config` does **not** catch it — it validates the config
without ever reading the credentials file.

## Why attachments and legacy buttons

An **attachment** `color` is the only thing in Slack that draws a coloured border — Block Kit
blocks have none — and the coloured border is the whole ask. Alertmanager's Slack notifier can
only emit attachments anyway (it has no `blocks` field), so the button is a legacy attachment
action and the click arrives as an `interactive_message`.

That routes through the gateway unchanged: Bolt's `app.action(ANY_ID)` matches attachment actions
by `callback_id`, and the gateway's `namespace_of()` already falls through to the top-level
`callback_id`. The callback id is `alert:<service>`; the button's `name` is `alert:resolve`.

## Deviation from the gateway spec

Backends are supposed to persist the click durably before answering 200. The *click* path still
queues in memory: the work is a cosmetic message edit, Slack never replays interactivity, and the
button survives a lost click, so the cost of a crash mid-flight is that the post stays red and you
click again.

Note this reasoning does **not** extend to the resolve path, and `store.py` says so at length. A
lost `ts` is not a lost cosmetic edit — it is a post that becomes permanently unreachable, because
no click is coming to mint a fresh `response_url` for it.

## Why the edit goes through `response_url`

`chat.update` **cannot do this job.** The alert post is authored by an incoming webhook, and
Slack refuses the bot token an edit to it — `cant_update_message`, every time (verified against a
real post, 2026-07-23). So the click is answered on its `response_url` with
`replace_original: true`, and `chat.update` is kept only as the fallback for a post we *did*
author.

Its 30-minute expiry is not a constraint, and the reason is easy to get backwards: **the window
opens when the button is clicked, not when the alert was posted.** A click on a three-day-old
alert still yields a fresh response_url, and the worker spends it a second later.

## Run it

```bash
cp alert-ack/env.example env/alert-ack.env    # then fill the values in
make build && make up                          # from the dockprom root
make test-alert-ack
```

For the **button**: the gateway must have `alert: alert-ack` in `interactivity_by_prefix` and an
`alert-ack` backend pointing at `http://alert-ack-backend:8084/slack/dispatch`, and both containers
must be on the external `homelab-slack` network.

For the **alerting path**, all four of these or it stays off (the endpoint answers 503 and
Alertmanager counts the failure — half-configured is the one state not supported):

1. `ALERT_CHANNEL` and `ALERTMANAGER_WEBHOOK_TOKEN` in `env/alert-ack.env`.
2. `alertmanager/alert_ack_token` holding the same token, `chown 65534:65534`, `chmod 0400`.
3. alert-ack on `monitor-net` — Alertmanager lives there and cannot otherwise reach it.
4. Bot scopes `chat:write`, `reactions:write`, `channels:history` (`groups:history` if private),
   **and the bot invited to the channel** — otherwise `conversations.history` answers
   `not_in_channel` and the backlog sweep silently finds nothing.
