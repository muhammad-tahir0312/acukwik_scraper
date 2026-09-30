#!/usr/bin/env python3
"""Compatibility transform that preserves airport-scoped organization listings.

Older versions merged records by organization name. That destroyed the
airport-to-role relationship and could merge unrelated companies with the same
name. Canonicalization now happens conservatively in the ETL; this command is
therefore an intentional, validated pass-through.
"""

import json
import sys
from pathlib import Path


def merge_organizations(ingestion_file: str, output_file: str) -> None:
    input_path = Path(ingestion_file)
    output_path = Path(output_file)
    records = 0
    organizations = 0

    with input_path.open("r", encoding="utf-8") as source, output_path.open(
        "w", encoding="utf-8"
    ) as destination:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON on line {line_number}: {exc}") from exc

            if record.get("entity_type") == "organization":
                organizations += 1
                data = record.get("data") or {}
                if not data.get("associated_airports") or not data.get("roles"):
                    raise ValueError(
                        f"Organization on line {line_number} lacks airport or role scope"
                    )

            destination.write(json.dumps(record, ensure_ascii=False) + "\n")
            records += 1

    print(
        f"Preserved {records} records ({organizations} airport-scoped organization listings). "
        "Canonical organization merging is handled by the ETL."
    )


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python transform_organizations.py <input_jsonl> [output_jsonl]")
        raise SystemExit(1)

    input_file = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else input_file.replace(
        ".jsonl", "_preserved.jsonl"
    )
    merge_organizations(input_file, output_file)
    print(f"Output written to: {output_file}")
