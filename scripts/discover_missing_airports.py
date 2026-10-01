#!/usr/bin/env python3
"""List airports referenced by scraped nearby lists that are not in the input list.

AC-U-KWIK's country pages miss some airports (e.g. hyphenated countries such as
GUINEA-BISSAU, and heliports in regions only reachable through the search form).
Every scraped airport's full nearby list names its neighbours, so any airport
referenced there but absent from the input CSV(s) and the scraped output is
written to a CSV the scraper can run as a follow-up pass. Repeat until it
reports zero new airports.

Usage:
    python scripts/discover_missing_airports.py \
        --output-dir runs/full_X/output --input country_links_20261001.csv \
        --write runs/full_X/discovered_pass1.csv
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import re
import sys
from pathlib import Path
from typing import Dict, Iterable, Set

AIRPORT_ID = re.compile(r"/Airport-Info/([^/?#\"']+)", re.IGNORECASE)
FIELDS = ["ICAO", "Airport Name", "City", "State", "Country", "Airport Link", "Source ID", "Country Page"]


def source_id(link: str) -> str:
    match = AIRPORT_ID.search(link or "")
    return match.group(1).upper() if match else ""


def known_ids(csv_paths: Iterable[str]) -> Set[str]:
    ids: Set[str] = set()
    for path in csv_paths:
        with open(path, newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                ident = (row.get("Source ID") or source_id(row.get("Airport Link") or row.get("url") or "")).upper()
                if ident:
                    ids.add(ident)
    return ids


def scan_output(output_dirs: Iterable[str]):
    """Return (scraped airport IDs, {referenced ID: example nearby row})."""
    scraped: Set[str] = set()
    referenced: Dict[str, Dict[str, str]] = {}
    for directory in output_dirs:
        for path in sorted(glob.glob(str(Path(directory) / "scraped_records_*.jsonl"))):
            with open(path, encoding="utf-8") as handle:
                for line in handle:
                    record = json.loads(line)
                    entity_type = record.get("entity_type")
                    if entity_type == "airport":
                        ident = source_id(record.get("url") or "") or (record.get("data") or {}).get("source_airport_id") or ""
                        if ident:
                            scraped.add(ident.upper())
                    elif entity_type == "nearby_airports":
                        for row in (record.get("data") or {}).get("nearby_airports") or []:
                            ident = source_id(row.get("url") or "") or (row.get("icao") or row.get("source_airport_id") or "")
                            ident = ident.upper()
                            if ident and ident not in referenced:
                                referenced[ident] = {
                                    "name": row.get("name") or "",
                                    "city": row.get("city") or "",
                                    "referenced_by": record.get("external_id") or "",
                                }
    return scraped, referenced


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output-dir", action="append", required=True, help="scraper output dir (repeatable)")
    parser.add_argument("--input", action="append", required=True, help="input CSV already scraped/queued (repeatable)")
    parser.add_argument("--write", type=Path, required=True, help="CSV of newly discovered airports")
    args = parser.parse_args(argv)

    known = known_ids(args.input)
    scraped, referenced = scan_output(args.output_dir)
    missing = sorted(set(referenced) - known - scraped)

    args.write.parent.mkdir(parents=True, exist_ok=True)
    with open(args.write, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        for ident in missing:
            info = referenced[ident]
            writer.writerow({
                "ICAO": ident if re.fullmatch(r"[A-Z]{4}", ident) else "",
                "Airport Name": info["name"],
                "City": info["city"],
                "State": "",
                "Country": "",
                "Airport Link": f"https://acukwik.com/Airport-Info/{ident}",
                "Source ID": ident,
                "Country Page": f"nearby:{info['referenced_by']}",
            })
    print(json.dumps({
        "known_input_ids": len(known),
        "scraped_airports": len(scraped),
        "referenced_in_nearby": len(referenced),
        "new_airports": len(missing),
        "sample": missing[:20],
        "written": str(args.write),
    }, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
