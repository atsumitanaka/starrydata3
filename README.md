# Starrydata3

A private, **KMDS-based** materials-data service — a self-hosted, always-local
successor to the Starrydata web app, with a proper **write API** and a full web
UI. Digitization happens in
[AutoLineDigitizer](https://github.com/adityaaa-IIT-BHU/AutoLineDigitizer)
(the desktop app), which pushes verified figures here with one button — nothing
ever has to leave your machine.

The canonical stored object is a full **KMDS record** (the relational schema:
`publication → samples / figures → graphs / materials`). Records are validated
against the KMDS schema on ingest and stored whole; their digitized curves AND
single measured values (material property trees, data tables) are indexed so
the classic property-pair query (*Temperature vs ZT*) works across papers.

## Features

- **Database** — searchable paper list (title / DOI / property / elements, e.g. `Bi Te`), per-paper samples, curves, and measured-value counts.
- **Record view** — abstract + keywords, per-property bar charts of measured values, digitized-curve plots, KMDS tables, samples, materials, figures & graphs.
- **Full KMDS viewer** (`/kmds/{sid}`) — the complete editorial record viewer (overview, materials property trees, conclusions, passages, references…), auto-loaded per record.
- **Explorer** — every curve of a property pair across all papers, plus measured values as point markers; log axes; CSV download of any plot.
- **Exports** — one-click full-database download as KMDS JSON (complete backup; re-upload to restore) or long-form CSV of every curve point and measured value.
- **Vocabulary honesty** — axis/table properties are matched to the official KMDS vocabulary (248 terms) plus clearly-labeled local extensions (`kmds_vocab_extensions.json`, e.g. electrochemical terms official KMDS lacks). Unmatched terms are stored verbatim and flagged — never silently renamed.
- **Safe upserts** — records upsert by DOI; a digitization-only upload (curves, no metadata) is **merged into** an existing record, never allowed to clobber it.

## Install

```bash
git clone https://github.com/adityaaa-IIT-BHU/starrydata3.git
cd starrydata3
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m starrydata3.manage initdb
.venv/bin/python -m starrydata3.manage seedprops                  # KMDS vocabulary (+ extensions)
.venv/bin/python -m starrydata3.manage createkey me --scope write # prints your API key — save it
.venv/bin/uvicorn starrydata3.app:app --host 127.0.0.1 --port 8300
```

Open http://localhost:8300 — interactive API docs at `/docs`.

### Digitizing papers

Install [AutoLineDigitizer](https://github.com/adityaaa-IIT-BHU/AutoLineDigitizer)
(see its README — the ML models run fully on your laptop). Open a paper PDF
there, verify each figure (axes, curve labels, KMDS terms), then press
**Upload approved → Starrydata3** with this server's URL + your API key.
Digitization-only uploads merge into existing records; they never overwrite
stored metadata.

## API

All write endpoints require `X-API-Key: <key>`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | liveness + record count |
| POST | `/api/v1/records` | ingest a KMDS record (upsert by DOI; stubs merge) |
| POST | `/api/v1/validate` | validate without storing |
| GET | `/api/v1/records` / `/{sid}` | list / fetch full KMDS record |
| GET | `/api/v1/graphs?prop_x=&prop_y=` | property-pair query across papers |
| GET | `/ui/export.json` · `/ui/export.csv` | full backup / long-form dataset |

## Uploading

- **Web**: the Upload tab accepts a KMDS `.json` or an AutoLineDigitizer `paper_record_view.html`.
- **CLI**: `python sd3_upload.py record.json --url http://localhost:8300 --key sd3_…`
- **From AutoLineDigitizer**: the desktop app has an *Upload approved → Starrydata3* button (URL + key in its UI).

## Notes

- `digitization_data` on graphs is a Starrydata3/AutoLineDigitizer extension of
  the official KMDS schema (which has only a text `digitization` note); the
  bundled schema accepts it, so such records validate cleanly.
- Vocabulary extensions live in `kmds_vocab_extensions.json` — add terms there
  and rerun `seedprops` (additive). Delete entries when official KMDS adopts
  them. Keep the copy in AutoLineDigitizer/src in sync.
- API keys are stored only as SHA-256 hashes — a key is shown once at creation.
- SQLite DB at `$SD3_DB` (default: repo root). If you delete/recreate the DB
  file, restart the server (it holds the old file handle).

## Tests

```bash
.venv/bin/python -m pytest tests/ -q
```
