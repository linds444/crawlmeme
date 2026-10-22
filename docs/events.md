# Scan event stream (Phase 4)

A scan is six crawlers walking the top holders. Every step they take is an event. This document is the contract for
how those events reach a client.

## Getting the events

| Request | Answer | Use |
|---|---|---|
| `POST /api/scan` with `Accept: text/event-stream` | Server-Sent Events in the same response, from start to result | the page |
| `GET /api/stream?job=ID` (`Last-Event-ID` or `&after=N` to resume) | Server-Sent Events of an existing job | a second viewer, `EventSource`, reconnects |
| `POST /api/scan` (JSON) then `GET /api/events?job=ID&after=N` | JSON polling, unchanged from V1 | the Telegram bot, scripts |

Why the scan request itself streams: on Vercel a scan runs inside the request that started it, and instances share
no memory. A separate stream request could land on another instance, which has no job to follow. Streaming the
events in the response that runs the scan works on one server and on serverless alike.

Errors that happen before a scan starts are not streamed. They are plain JSON with their usual status:
400 bad address, 409 `ambiguous_chain`, 429 rate limited, 503 busy (including `STREAMS_MAX` open streams).

## Frames

```
retry: 2000

event: job
data: {"job":"…","chain":"bsc","token":"0x…"}

id: 0
event: scan
data: {"i":0,"t":12,"type":"stage","spider":null,"wallet":null,"detail":"launch","chain":"bsc"}

: ping

event: result
data: {"done":true,"result":{…}}        (or {"done":true,"error":"…"})
```

- `job` comes first. It carries the job id and the chain the address resolved to.
- `scan` carries one engine event per frame. `id` is the event index `i`, so a reconnecting client sends
  `Last-Event-ID` and gets only what it missed.
- `: ping` is sent every `STREAM_HEARTBEAT_S` (10 s) while no event arrives, so proxies keep the connection open.
- `result` is the same body `/api/result` returns. The connection closes after it.
- `timeout` (`{"job","after"}`) means the stream hit `STREAM_MAX_S` (120 s) before the job finished. Reconnect with
  `/api/stream`.

## Event types (`scan` frames)

Every event has `i` (index), `t` (ms since the scan started), `type`, `spider` (crawler 0–5 or null), `wallet`,
`detail` and `chain`.

| type | when | extra fields |
|---|---|---|
| `stage` | a step starts: `launch`, `transfers`, `entries`, `wallets`, `links` | — |
| `spider_move` | a crawler is sent to a holder | — |
| `wallet_flag` | a holder's entry and history are read | `flags`, `kind`, `share`, `share_supply` |
| `link` | two holders are linked | `b`, `level`, `kind`, `via` |
| `cluster` | linked holders merge into one operator | `wallets`, `level`, `share`, `share_supply` |
| `done` | verdict | `score`, `band`, `headline`, `rug` |
| `error` | the scan failed; `detail` is the reason | — |

## Limits and security

- The number of open streams is capped by `STREAMS_MAX` (default 64), because each stream holds a server thread.
- No stream outlives `STREAM_MAX_S`.
- Responses are `cache-control: no-store` and `x-accel-buffering: no`, so proxies forward each frame as it is
  written.
- The stream carries only what `/api/events` and `/api/result` already return. It adds no new data exposure.
- Joining a job (`/api/stream`) never starts a scan on a long-running server. On serverless, an unknown job id is
  re-scanned, as `/api/events` already does, under the same rate limit.
