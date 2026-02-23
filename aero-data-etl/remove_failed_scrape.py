import json

input_path = "airport details.jsonl"
output_path = "airport details.cleaned.jsonl"

with open(input_path, "r", encoding="utf-8") as infile, open(output_path, "w", encoding="utf-8") as outfile:
    for line in infile:
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
            if obj.get("scrape_status") == "FAILED":
                continue
            # Also check if nested in 'data' or other structure if needed
            outfile.write(json.dumps(obj) + "\n")
        except Exception as e:
            # Optionally log or skip malformed lines
            continue
print(f"Cleaned file written to {output_path}")
