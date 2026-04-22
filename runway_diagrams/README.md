# Runway Diagram Downloader and Cleaner

This folder downloads Acukwik runway diagrams by ICAO and creates cropped images that keep only the runway layout area.

## command to run

cd '/media/tahir/Local Disk/OFFICE/acukwik_scraper' && '/media/tahir/Local Disk/OFFICE/acukwik_scraper/selenium_ingestion_final/venv/bin/python' runway_diagrams/download_and_clean.py --input-csv country_links.csv --workers 20

## What it creates

- `runway_diagrams/raw/<ICAO>.jpg` - original downloaded image
- `runway_diagrams/cleaned/<ICAO>.jpg` - cropped runway-focused image
- `runway_diagrams/manifest.csv` - mapping table to link airport and image later

By default, `manifest.csv` includes only airports where image download and cleaning succeeded.

`manifest.csv` contains:

- `icao`
- `airport_name`
- `city`
- `state`
- `country`
- `airport_link`
- `source_url`
- `raw_image`
- `cleaned_image`
- `status`
- `error`
- `crop_bbox`

## Install extra dependencies

From project root:

```bash
pip install opencv-python-headless numpy
```

## Test with a small sample first

```bash
python runway_diagrams/download_and_clean.py --input-csv country_links.csv --limit 20 --workers 4
```

## Run full dataset

```bash
python runway_diagrams/download_and_clean.py --input-csv country_links.csv --workers 8
```

## If you also want failed rows in manifest

```bash
python runway_diagrams/download_and_clean.py --input-csv country_links.csv --workers 8 --include-failed
```

## Notes

- URL pattern used: `https://acukwik.com/extimages/Listing-Images/<ICAO>.jpg`
- Rows with missing/invalid ICAO are skipped.
- Branding/footer text is reduced by auto-cropping to detected runway diagram components.
- A few airports may still need manual touch-up if the auto-crop is not perfect.
