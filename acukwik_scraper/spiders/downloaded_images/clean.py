
import json
import csv

# Paths
json_path = "/home/dani/Desktop/Ikhwa/acukwik-scraper/acukwik_scraper/spiders/scraped_outputs/USA_airport_links_7k_output.json"
csv_path = "/home/dani/Desktop/Ikhwa/acukwik-scraper/acukwik_scraper/spiders/countries/USA_airport_links_7k.csv"
output_path = "/home/dani/Desktop/Ikhwa/acukwik-scraper/acukwik_scraper/spiders/to_scrape_cleaned.csv"

# Step 1: Load all existing links from JSON
with open(json_path, "r", encoding="utf-8") as f:
    data = json.load(f)

existing_links = set()
for airport in data:
    details = airport.get("Airport Details", {})
    link = details.get("Link")
    if link:
        existing_links.add(link.strip())

print(f"Found {len(existing_links)} existing links in JSON")

# Step 2: Read CSV and filter rows
with open(csv_path, "r", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    rows = [row for row in reader if row["Airport Link"].strip() not in existing_links]

print(f"Filtered {len(rows)} rows remaining out of {reader.line_num - 1}")

# Step 3: Write cleaned CSV
with open(output_path, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=reader.fieldnames)
    writer.writeheader()
    writer.writerows(rows)

print(f"✅ Cleaned CSV saved to {output_path}")
