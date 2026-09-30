#!/bin/bash
# Run from any directory; use the shared environment without deleting it.
set -euo pipefail
ETL_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ETL_VENV="${ETL_VENV_DIR:-$ETL_DIR/../.venv}"
if [ ! -x "$ETL_VENV/bin/python" ]; then
  python3 -m venv "$ETL_VENV"
fi
if ! "$ETL_VENV/bin/python" -c 'import psycopg2, dotenv' 2>/dev/null; then
  "$ETL_VENV/bin/python" -m pip install -r "$ETL_DIR/requirements.txt"
fi
export PYTHONPATH="$ETL_DIR${PYTHONPATH:+:$PYTHONPATH}"
exec "$ETL_VENV/bin/python" -m etl.main "$@"
