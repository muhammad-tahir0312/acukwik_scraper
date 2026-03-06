#!/bin/bash
# Crash-safe ETL runner: creates venv, installs dependencies, runs ETL
set -e

VENV_DIR=".venv"
REQUIREMENTS="psycopg2-binary"

# Create venv if not exists or if pip shebang is broken (e.g. venv was moved)
VENV_PIP="$VENV_DIR/bin/pip"
if [ ! -d "$VENV_DIR" ] || [ ! -f "$VENV_PIP" ] || ! "$VENV_PIP" --version &>/dev/null; then
  rm -rf "$VENV_DIR"
  python3 -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"

# Install required libraries using venv pip explicitly
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install $REQUIREMENTS

# Run ETL (auto-detects files in acukwik_data)
"$VENV_DIR/bin/python" -m etl.main
