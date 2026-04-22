#!/usr/bin/env python3
"""Download and clean Acukwik runway diagram images by ICAO.

Workflow:
1) Read ICAO codes from a CSV (expects an ICAO column).
2) Download raw image from https://acukwik.com/extimages/Listing-Images/{ICAO}.jpg
3) Auto-crop to runway-diagram region to remove title/footer branding text as much as possible.
4) Write a manifest CSV for reliable airport-to-image linking later.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Dict, Iterable, List, Tuple

import cv2
import numpy as np
import requests

BASE_URL = "https://acukwik.com/extimages/Listing-Images/{icao}.jpg"
ICAO_RE = re.compile(r"^[A-Z0-9]{4}$")


@dataclass
class AirportRow:
    icao: str
    airport_name: str
    city: str
    state: str
    country: str
    airport_link: str


def read_airports(csv_path: str) -> List[AirportRow]:
    airports: List[AirportRow] = []
    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            icao = (row.get("ICAO") or "").strip().upper()
            if not ICAO_RE.match(icao):
                continue
            airports.append(
                AirportRow(
                    icao=icao,
                    airport_name=(row.get("Airport Name") or "").strip(),
                    city=(row.get("City") or "").strip(),
                    state=(row.get("State") or "").strip(),
                    country=(row.get("Country") or "").strip(),
                    airport_link=(row.get("Airport Link") or "").strip(),
                )
            )

    dedup: Dict[str, AirportRow] = {}
    for airport in airports:
        dedup[airport.icao] = airport
    return list(dedup.values())


def ensure_dirs(paths: Iterable[str]) -> None:
    for path in paths:
        os.makedirs(path, exist_ok=True)


def download_image(session: requests.Session, url: str, out_path: str, timeout: int = 30) -> None:
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    content_type = response.headers.get("Content-Type", "").lower()
    if "image" not in content_type:
        raise ValueError(f"Not an image response: {content_type}")
    with open(out_path, "wb") as f:
        f.write(response.content)


def find_runway_bbox(image: np.ndarray) -> Tuple[int, int, int, int]:
    """Return x1, y1, x2, y2 around the runway diagram region.

    The heuristic keeps long/thick connected components (runway shapes),
    then unions them to avoid footer text/logos.
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape[:2]

    # Dark pixels are likely runway graphics or text.
    threshold_value = int(np.percentile(gray, 42))
    mask = (gray < threshold_value).astype(np.uint8) * 255

    # Make components denser so runway structures stay connected.
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=1)

    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)

    candidates: List[Tuple[int, int, int, int]] = []
    min_area = int(h * w * 0.002)
    for label in range(1, num_labels):
        x, y, cw, ch, area = stats[label]

        # Reject tiny text pieces.
        if area < min_area:
            continue

        # Keep elongated or large shapes that resemble runway graphics.
        elongated = (cw > w * 0.18 and ch > h * 0.02) or (ch > h * 0.18 and cw > w * 0.02)
        broad = cw > w * 0.35 or ch > h * 0.25
        if elongated or broad:
            candidates.append((x, y, x + cw, y + ch))

    if not candidates:
        # Safe fallback: central crop where runway diagram typically appears.
        return int(w * 0.07), int(h * 0.10), int(w * 0.93), int(h * 0.78)

    x1 = min(c[0] for c in candidates)
    y1 = min(c[1] for c in candidates)
    x2 = max(c[2] for c in candidates)
    y2 = max(c[3] for c in candidates)

    # Padding around detected diagram.
    pad_x = int(0.03 * w)
    pad_y = int(0.03 * h)
    x1 = max(0, x1 - pad_x)
    # Keep airport title area (top-left) by allowing a shallower top trim.
    y1 = max(int(0.05 * h), y1 - pad_y)
    x2 = min(w, x2 + pad_x)
    y2 = min(int(0.79 * h), y2 + pad_y)  # Avoid footer branding band.

    if x2 <= x1 or y2 <= y1:
        return int(w * 0.07), int(h * 0.06), int(w * 0.93), int(h * 0.77)

    return x1, y1, x2, y2


def remove_bottom_right_branding(cropped: np.ndarray) -> np.ndarray:
    """Remove footer branding text near bottom-right while keeping title area untouched."""
    h, w = cropped.shape[:2]
    if h < 40 or w < 40:
        return cropped

    # Target only the region where footer branding usually appears.
    roi_y1 = int(h * 0.82)
    roi_x1 = int(w * 0.62)
    roi = cropped[roi_y1:h, roi_x1:w]
    if roi.size == 0:
        return cropped

    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    thresh = min(125, int(np.percentile(gray, 35)))
    raw_mask = (gray < thresh).astype(np.uint8) * 255

    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(raw_mask, connectivity=8)
    text_mask = np.zeros_like(raw_mask)
    roi_area = roi.shape[0] * roi.shape[1]

    # Keep only small/medium connected components that look like text glyphs.
    for label in range(1, num_labels):
        x, y, cw, ch, area = stats[label]
        if area < 8:
            continue
        if area > max(roi_area * 0.08, 2500):
            continue
        if cw > roi.shape[1] * 0.85 or ch > roi.shape[0] * 0.8:
            continue
        text_mask[labels == label] = 255

    if np.count_nonzero(text_mask) == 0:
        return cropped

    cleaned_roi = cv2.inpaint(roi, text_mask, inpaintRadius=3, flags=cv2.INPAINT_TELEA)
    out = cropped.copy()
    out[roi_y1:h, roi_x1:w] = cleaned_roi
    return out


def clean_runway_image(raw_path: str, cleaned_path: str) -> Tuple[int, int, int, int]:
    image = cv2.imread(raw_path)
    if image is None:
        raise ValueError("Could not decode image")

    # Keep full frame to preserve runway geometry and top-left airport name.
    h, w = image.shape[:2]
    cleaned = image.copy()

    gray = cv2.cvtColor(cleaned, cv2.COLOR_BGR2GRAY)
    threshold_value = min(125, int(np.percentile(gray, 40)))
    dark_mask = (gray < threshold_value).astype(np.uint8) * 255

    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(dark_mask, connectivity=8)
    text_mask = np.zeros_like(dark_mask)

    for label in range(1, num_labels):
        x, y, cw, ch, area = stats[label]

        # Ignore tiny noise and very large structural components.
        if area < 8 or area > int(h * w * 0.08):
            continue

        # Keep title area untouched by skipping upper region components.
        if y < int(h * 0.22):
            continue

        # Target likely branding zones: lower-right and lower-center right.
        in_brand_zone = (
            (y > int(h * 0.80) and x > int(w * 0.35))
            or (y > int(h * 0.55) and x > int(w * 0.55))
        )
        if not in_brand_zone:
            continue

        # Prefer text-like components, not thick runway bars.
        text_like = ch < int(h * 0.18) and cw < int(w * 0.70)
        if not text_like:
            continue

        text_mask[labels == label] = 255

    if np.count_nonzero(text_mask) > 0:
        # Expand text mask slightly so glyph edges are removed cleanly.
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        text_mask = cv2.dilate(text_mask, kernel, iterations=1)
        cleaned = cv2.inpaint(cleaned, text_mask, inpaintRadius=3, flags=cv2.INPAINT_TELEA)

    cv2.imwrite(cleaned_path, cleaned)
    return 0, 0, w, h


def process_one(
    session: requests.Session,
    airport: AirportRow,
    raw_dir: str,
    cleaned_dir: str,
) -> Dict[str, str]:
    icao = airport.icao
    url = BASE_URL.format(icao=icao)
    raw_path = os.path.join(raw_dir, f"{icao}.jpg")
    cleaned_path = os.path.join(cleaned_dir, f"{icao}.jpg")

    record = {
        "icao": icao,
        "airport_name": airport.airport_name,
        "city": airport.city,
        "state": airport.state,
        "country": airport.country,
        "airport_link": airport.airport_link,
        "source_url": url,
        "raw_image": raw_path,
        "cleaned_image": cleaned_path,
        "status": "",
        "error": "",
        "crop_bbox": "",
    }

    try:
        download_image(session, url, raw_path)
        x1, y1, x2, y2 = clean_runway_image(raw_path, cleaned_path)
        record["crop_bbox"] = f"{x1},{y1},{x2},{y2}"
        record["status"] = "ok"
    except Exception as exc:
        record["status"] = "failed"
        record["error"] = str(exc)

    return record


def write_manifest(path: str, rows: List[Dict[str, str]]) -> None:
    if not rows:
        return
    fields = list(rows[0].keys())
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download and clean Acukwik runway diagrams")
    parser.add_argument(
        "--input-csv",
        default="country_links.csv",
        help="Path to source CSV containing ICAO column",
    )
    parser.add_argument(
        "--output-dir",
        default="runway_diagrams",
        help="Base output folder for raw/cleaned images and manifest",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Optional limit for testing (0 means no limit)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=8,
        help="Parallel download workers",
    )
    parser.add_argument(
        "--include-failed",
        action="store_true",
        help="Also write failed rows into manifest (default stores only successful airports)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    raw_dir = os.path.join(args.output_dir, "raw")
    cleaned_dir = os.path.join(args.output_dir, "cleaned")
    manifest_path = os.path.join(args.output_dir, "manifest.csv")

    ensure_dirs([args.output_dir, raw_dir, cleaned_dir])

    airports = read_airports(args.input_csv)
    if args.limit and args.limit > 0:
        airports = airports[: args.limit]

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Referer": "https://acukwik.com/",
    }

    results: List[Dict[str, str]] = []
    with requests.Session() as session:
        session.headers.update(headers)

        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            futures = [
                pool.submit(process_one, session, airport, raw_dir, cleaned_dir)
                for airport in airports
            ]
            for future in as_completed(futures):
                results.append(future.result())

    # Stable order by ICAO for easier linking downstream.
    results.sort(key=lambda r: r["icao"])
    successful = [r for r in results if r["status"] == "ok"]
    manifest_rows = results if args.include_failed else successful
    write_manifest(manifest_path, manifest_rows)

    total = len(results)
    ok = sum(1 for r in results if r["status"] == "ok")
    failed = total - ok
    print(f"Done. total={total}, ok={ok}, failed={failed}")
    print(f"Stored rows in manifest={len(manifest_rows)}")
    print(f"Manifest: {manifest_path}")


if __name__ == "__main__":
    main()
