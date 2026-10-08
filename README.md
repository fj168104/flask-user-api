# flask-user-api

Flask 3 backend providing user registration/login (Redis-backed bearer tokens)
and two independent OCR providers sharing the same `{code, msg, data}` JSON
envelope:

- `POST /api/ocr` — **Google Cloud Vision** (authenticated, image OCR)
- `POST /api/v1/ocr/extract` — **TextIn xparse** (unauthenticated, full
  document OCR with optional schema-driven field extraction)

## Quick start

```bash
pip install -r requirements-dev.txt
python app.py            # dev server on :5000
```

Tests:

```bash
pytest tests/textin -v
```

## Configuration (`.env`)

```dotenv
# Google Vision
GOOGLE_VISION_API_KEY=...

# TextIn OCR
TEXTIN_APP_ID=...
TEXTIN_SECRET_CODE=...
TEXTIN_ENDPOINT=https://api.textin.com/api/v1/xparse/parse/sync
TEXTIN_EXTRACT_ENDPOINT=https://api.textin.com/api/xparse/extract/sync
OCR_STORAGE_DIR=./temp/extractions
OCR_UPLOADS_DIR=./temp/uploads
TEXTIN_TIMEOUT_SECONDS=60
TEXTIN_CONNECT_TIMEOUT_SECONDS=10
```

`TEXTIN_APP_ID` / `TEXTIN_SECRET_CODE` may be omitted; the app will still
start, but `POST /api/v1/ocr/extract` returns 503. List / detail / file
endpoints continue to work from local storage.

## Endpoints

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| GET | `/api/health` | – | Liveness |
| POST | `/api/register` | – | Create user |
| POST | `/api/login` | – | Returns bearer token |
| GET | `/api/user` | bearer | Current user |
| POST | `/api/logout` | bearer | Revoke token |
| POST | `/api/ocr` | bearer | Google Vision OCR (image) |
| POST | `/api/v1/ocr/extract` | – | TextIn OCR (file or URL) |
| GET | `/api/v1/ocr/extractions` | – | List records |
| GET | `/api/v1/ocr/extractions/<id>` | – | Record detail |
| GET | `/api/v1/ocr/extractions/<id>/file` | – | Original file download |
| GET | `/api/uploads/<filename>` | – | Serve Google Vision upload |

## TextIn provider details

- Storage: `./temp/extractions/<uuid>.json` (records) and
  `./temp/uploads/<uuid>.<ext>` (originals). Atomic write via tmp + replace.
- Field extraction strategies: heuristic (markdown + tables) by default;
  schema-driven (`field_names=…`) when caller supplies field names. URL
  sources always use heuristic (TextIn schema endpoint requires a local
  file).
- `extraction_id` is a v4 UUID. Non-UUID path segments return 404 (no
  filesystem lookup, no path traversal).
- File extensions `.htm/.html/.svg/.js` are normalized to `.bin` on disk.
- Persistence is local JSON — **a `/app/temp` volume is required in
  production** so records and uploads survive container replacement.

> ⚠️ The TextIn endpoints are intentionally unauthenticated. The OCR calls
> are billable on the upstream TextIn account. Do not expose the routes
> publicly without a reverse-proxy rate limit / auth layer.

## Project layout

```
.
├── app.py            # user + Google Vision routes, factory
├── auth.py           # Redis-backed token issue/resolve/revoke
├── config.py         # Config (env + hardcoded MySQL/Redis)
├── extensions.py     # db, redis_client, init_textin
├── models.py         # User
├── ocr.py            # Google Cloud Vision client
├── textin/           # TextIn OCR provider (blueprint + clients)
│   ├── config.py     # TextInSettings (pydantic-settings)
│   ├── client.py     # synchronous TextInClient
│   ├── errors.py
│   ├── field_extractor.py
│   ├── routes.py     # Flask blueprint
│   ├── schemas.py    # Pydantic success models
│   └── service.py    # orchestration + persistence
├── tests/textin/     # unit + integration tests
└── temp/             # runtime-only OCR storage (gitignored)
```