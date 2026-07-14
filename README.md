# Starrydata3

A private, **KMDS-based** materials-data service with a proper **write API**.

Starrydata3 is a small self-hosted successor to the Starrydata web app. The
canonical stored object is a full **KMDS record** (sensei's relational schema:
`publication → samples / figures → graphs / materials`). Records are validated
against the KMDS schema on ingest, stored whole, and their digitized curves are
indexed so the classic **property-pair query** (e.g. *Temperature vs ZT*) works
— the same value Starrydata provides — but **writes go through a REST API with
API-key auth**, not a session-only web form.

It is built to receive [AutoLineDigitizer](../AutoLineDigitizer) output
directly: the merged record (`window.__STUDIO_RECORD` in `paper_record_view.html`)
is a valid input.

## Why this exists

Starrydata2 has no public write API — uploads reuse a logged-in browser session,
send undocumented internal requests, and there is no controlled property
vocabulary exposed. Starrydata3 fixes all three:

| Starrydata2 (uploading to it) | Starrydata3 |
|---|---|
| Session-only writes (no API) | REST API, **API keys** with read/write/admin scopes |
| Internal, undocumented endpoints | Documented OpenAPI at `/docs` |
| No property vocabulary check | Seeded with the **248 KMDS properties**; unknown terms reported |
| Free-text, LaTeX-unit issues | KMDS-schema validation on every record |

## Quick start (local)

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m starrydata3.manage initdb
.venv/bin/python -m starrydata3.manage seedprops                 # loads 248 KMDS properties
.venv/bin/python -m starrydata3.manage createkey ada --scope write   # prints an API key
.venv/bin/uvicorn starrydata3.app:app --host 0.0.0.0 --port 8300
```

Open http://localhost:8300/docs for interactive API docs.

## Web hosting

```bash
docker compose up -d            # gunicorn + uvicorn workers on :8300, data in a volume
# or, without Docker:
./run.sh                        # initdb + seedprops + gunicorn
```

Put it behind a reverse proxy (nginx/Caddy) with TLS for a real deployment.
The SQLite DB lives at `$SD3_DB` (a Docker volume by default); switch to
Postgres by changing the `create_engine` URL when you outgrow SQLite.

## API

All endpoints require `X-API-Key: <key>`. Writes need `write` scope; reads need `read`.

| Method | Path | Scope | Purpose |
|---|---|---|---|
| GET | `/health` | — | liveness + record count |
| POST | `/api/v1/records` | write | ingest a KMDS record (upsert by DOI, `?strict=true` to reject on violations) |
| POST | `/api/v1/validate` | read | validate a record against the KMDS schema without storing |
| GET | `/api/v1/records` | read | list records |
| GET | `/api/v1/records/{sid}` | read | full KMDS record back |
| GET | `/api/v1/graphs?prop_x=&prop_y=` | read | every digitized curve of a property pair |
| GET | `/api/v1/graph_list` | read | all property pairs + counts |
| GET | `/api/v1/properties` | read | the KMDS property vocabulary |

## Uploading from AutoLineDigitizer

```bash
python sd3_upload.py path/to/paper_record_view.json \
    --url http://your-server:8300 --key sd3_xxx
# dry-run against the schema first:
python sd3_upload.py record.html --url ... --key ... --validate
```

`sd3_upload.py` accepts a raw KMDS record `.json` or an AutoLineDigitizer
`paper_record_view.html` (it extracts `window.__STUDIO_RECORD`). Curves on
placeholder-axis stub graphs are matched to their real KMDS graph by
value-range before indexing, so nothing is lost.

## Notes

- **`digitization_data`** (where the curve points live) is an AutoLineDigitizer
  extension, not part of the official KMDS schema, so records carrying it report
  schema violations. Ingest is non-strict by default (stored + violations
  reported); use `?strict=true` to reject. A future KMDS revision that defines a
  digitized-data field would remove these.
- API keys are stored only as SHA-256 hashes — a key is shown once at creation.

## Tests

```bash
.venv/bin/python -m pytest tests/ -q
```
