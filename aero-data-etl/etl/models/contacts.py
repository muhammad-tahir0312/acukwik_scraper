"""
Contact insert queries. Never overwrite existing contacts.
"""
CONTACT_INSERT = """
INSERT INTO contacts (entity_id, type, value, label)
VALUES (%s, %s, %s, %s)
ON CONFLICT (entity_id, type, value) DO NOTHING;
"""
