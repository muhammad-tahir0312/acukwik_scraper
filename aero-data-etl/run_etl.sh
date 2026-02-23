#!/bin/bash
# Crash-safe ETL runner: creates venv, installs dependencies, runs ETL
set -e

VENV_DIR=".venv"
REQUIREMENTS="psycopg2-binary"
INPUT_FILE="$1"

if [ -z "$INPUT_FILE" ]; then
  echo "Usage: ./run_etl.sh <input_jsonl>"
  exit 1
fi

# Create venv if not exists
if [ ! -d "$VENV_DIR" ]; then
  python3 -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"

# Install required libraries
pip install --upgrade pip
pip install $REQUIREMENTS

# Run ETL
python -m etl.main "$INPUT_FILE"
