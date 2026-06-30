# Static assets

This directory is served at `/static`. The HTMX templates expect two vendored
JavaScript files here (kept out of git on purpose — vendor them locally so the
shell has **no CDN dependency** and runs fully offline):

| file           | source                                                       |
|----------------|--------------------------------------------------------------|
| `htmx.min.js`  | https://unpkg.com/htmx.org@1.9.12/dist/htmx.min.js           |
| `sse.js`       | https://unpkg.com/htmx.org@1.9.12/dist/ext/sse.js            |

## Vendor them

```bash
cd challenge-pack/app/static
curl -L -o htmx.min.js https://unpkg.com/htmx.org@1.9.12/dist/htmx.min.js
curl -L -o sse.js      https://unpkg.com/htmx.org@1.9.12/dist/ext/sse.js
```

Notes:
- `base.html` loads both with `defer`.
- The chat console (`chat.html`) uses a small hand-rolled `fetch()` SSE reader for
  `POST /v1/chat`, so the `sse.js` extension is optional there but kept for any
  `hx-ext="sse"` GET-based streaming you add.
- Page images for the document viewer are served by the app from `example/images/`
  via `GET /documents/{doc_id}/image` — they are **not** copied here.
