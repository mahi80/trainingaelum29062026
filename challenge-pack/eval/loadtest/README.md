# Chat load test (k6)

`chat_loadtest.js` drives `POST /v1/chat` at a constant arrival rate to verify the
service holds its latency and error-rate budget under sustained traffic.

## Why `stream=false` and tiny prompts

The latency budget — `http_req_duration p(95) < 150 ms` — is meant to measure the
**request pipeline** (auth, session lookup, routing, retrieval, SQL), *not* LLM
token generation, which is inherently slow and variable. To keep the measurement
honest the script always sends `stream=false` with very short prompts, so the
model does minimal work and the p95 reflects everything except generation. Token
throughput / time-to-first-token should be measured separately, not here.

## Install k6

- macOS: `brew install k6`
- Debian/Ubuntu: see https://grafana.com/docs/k6/latest/set-up/install-k6/
- Docker: `docker run --rm -i grafana/k6 run - < chat_loadtest.js`

## Run

```bash
# defaults: 50 RPS for 1m against http://localhost:8000
k6 run eval/loadtest/chat_loadtest.js

# point at another host, authenticate, change the rate/duration
BASE_URL=http://localhost:8000 \
API_TOKEN="$(cat .token)" \
RATE=50 DURATION=2m \
k6 run eval/loadtest/chat_loadtest.js
```

## Tunables (environment variables)

| Var         | Default                  | Meaning                                   |
|-------------|--------------------------|-------------------------------------------|
| `BASE_URL`  | `http://localhost:8000`  | Service base URL.                         |
| `RATE`      | `50`                     | Target requests/second (constant rate).   |
| `DURATION`  | `1m`                     | Sustained load window.                     |
| `API_TOKEN` | _(empty)_                | Optional OAuth2 bearer token.             |

## Thresholds (enforced)

| Threshold                        | Budget    | Notes                                    |
|----------------------------------|-----------|------------------------------------------|
| `http_req_duration` p(95)        | < 150 ms  | Excludes token generation (stream=false).|
| `http_req_failed` rate           | < 0.01    | < 1% of requests may fail.               |

k6 exits non-zero if any threshold is breached, so this script can gate CI.

## Headers

Each request carries `Idempotency-Key` and `X-Correlation-ID` per
`openapi/contract.yaml`, so server-side correlation logging is exercised under
load.
