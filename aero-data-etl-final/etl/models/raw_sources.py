"""
Raw source models for storing verbatim scraped JSON.
"""
RAW_SCRAPED_INSERT = """
INSERT INTO scraped_records (
	entity_type, external_id, url, scrape_status, data,
	observed_fields, missing_fields, validation_errors, errors
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
RETURNING id;
"""
