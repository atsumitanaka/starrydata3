#!/usr/bin/env bash
# Local (non-Docker) production run.
set -e
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt gunicorn
.venv/bin/python -m starrydata3.manage initdb
.venv/bin/python -m starrydata3.manage seedprops
exec .venv/bin/gunicorn starrydata3.app:app -k uvicorn.workers.UvicornWorker \
     --bind 0.0.0.0:8300 --workers 2
