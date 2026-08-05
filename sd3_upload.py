#!/usr/bin/env python3
"""
sd3_upload.py — push a NCMRD record (e.g. from AutoLineDigitizer) into a
Starrydata3 server through its REST API with an API key. No browser, no
session — just an HTTP call.

    python sd3_upload.py record.json --url http://localhost:8300 --key sd3_...
    python sd3_upload.py record.json --url ... --key ... --validate   # dry-run

Accepts either a raw NCMRD record JSON, or an AutoLineDigitizer
paper_record_view.html (the merged window.__STUDIO_RECORD is extracted).
"""
import argparse
import json
import re
import sys

import httpx


def load_record(path: str) -> dict:
    text = open(path, encoding="utf-8").read()
    if path.endswith(".html"):
        m = re.search(r"window\.__STUDIO_RECORD\s*=\s*(\{.*?\});\s*\n", text, re.S)
        if not m:
            sys.exit("no window.__STUDIO_RECORD found in the HTML")
        return json.loads(m.group(1))
    return json.loads(text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("record")
    ap.add_argument("--url", required=True, help="Starrydata3 base URL")
    ap.add_argument("--key", required=True, help="API key (sd3_...)")
    ap.add_argument("--validate", action="store_true",
                    help="dry-run: validate against NCMRD schema without storing")
    ap.add_argument("--strict", action="store_true",
                    help="reject on any schema violation")
    args = ap.parse_args()

    record = load_record(args.record)
    doi = ((record.get("metadata") or {}).get("publication") or {}).get("DOI")
    endpoint = "/api/v1/validate" if args.validate else "/api/v1/records"
    print(f"{'VALIDATE' if args.validate else 'UPLOAD'} DOI={doi} -> {args.url}{endpoint}")

    params = {"strict": "true"} if args.strict and not args.validate else None
    r = httpx.post(args.url.rstrip("/") + endpoint, json=record, params=params,
                   headers={"X-API-Key": args.key}, timeout=120)
    try:
        body = r.json()
    except Exception:
        body = {"status": r.status_code, "text": r.text[:400]}
    print(f"HTTP {r.status_code}")
    print(json.dumps(body, ensure_ascii=False, indent=2)[:1500])
    sys.exit(0 if r.status_code < 400 else 1)


if __name__ == "__main__":
    main()
