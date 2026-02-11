#!/usr/bin/env python3
"""
Transformation layer: Deduplicate and merge organizations.
Reads JSONL from ingestion, outputs merged organization records.
"""
import json
from pathlib import Path
from typing import Dict, List, Any
from collections import defaultdict


def merge_organizations(ingestion_file: str, output_file: str):
    """
    Merge organization records from multiple airports into single entities.
    
    Args:
        ingestion_file: JSONL file from scraper (raw ingestion)
        output_file: JSONL file with merged organizations
    """
    # Read all records
    organizations_by_name = defaultdict(list)
    airports = []
    
    with open(ingestion_file, 'r', encoding='utf-8') as f:
        for line in f:
            record = json.loads(line)
            
            if record['entity_type'] == 'airport':
                airports.append(record)
            
            elif record['entity_type'] == 'organization':
                name = record['data']['name']
                organizations_by_name[name].append(record)
    
    # Merge organizations
    merged_orgs = []
    
    for org_name, records in organizations_by_name.items():
        if len(records) == 1:
            # Single location - keep as-is
            merged_orgs.append(records[0])
        else:
            # Multiple locations - merge
            merged = merge_org_records(org_name, records)
            merged_orgs.append(merged)
    
    # Write output
    with open(output_file, 'w', encoding='utf-8') as f:
        # Write airports first
        for airport in airports:
            f.write(json.dumps(airport, ensure_ascii=False) + '\n')
        
        # Write merged organizations
        for org in merged_orgs:
            f.write(json.dumps(org, ensure_ascii=False) + '\n')
    
    print(f"Merged {len(organizations_by_name)} unique organizations")
    print(f"  - {len([r for r in records if len(organizations_by_name[r['data']['name']]) == 1])} single-location")
    print(f"  - {len([n for n, r in organizations_by_name.items() if len(r) > 1])} multi-location")


def merge_org_records(org_name: str, records: List[Dict]) -> Dict:
    """
    Merge multiple records of same organization into single entity.
    
    Strategy:
    - Combine associated_airports lists
    - Keep most complete contact information
    - Store location-specific data separately
    """
    # Start with first record as base
    merged = records[0].copy()
    
    # Update external_id to remove airport-specific suffix
    from hashlib import md5
    merged['external_id'] = f"acukwik_org_{org_name.upper().replace(' ', '_')}"
    
    # Combine associated airports
    all_airports = set()
    for record in records:
        airports = record['data'].get('associated_airports', [])
        all_airports.update(airports)
    
    merged['data']['associated_airports'] = sorted(list(all_airports))
    
    # Merge contacts - keep most complete set
    all_contacts = []
    contacts_by_type = {}
    
    for record in records:
        contacts = record['data'].get('contacts', [])
        if contacts:
            for contact in contacts:
                contact_type = contact['type']
                # Keep first occurrence of each contact type (or most complete)
                if contact_type not in contacts_by_type:
                    contacts_by_type[contact_type] = contact
    
    merged['data']['contacts'] = list(contacts_by_type.values()) if contacts_by_type else None
    
    # Store location-specific variations if they differ
    location_specific = {}
    for record in records:
        airport = record['data']['associated_airports'][0]
        
        # Check if this location has unique data
        record_contacts = {c['type']: c['value'] for c in record['data'].get('contacts', [])}
        merged_contacts = {c['type']: c['value'] for c in merged['data'].get('contacts', [])}
        
        if record_contacts != merged_contacts:
            location_specific[airport] = {
                'contacts': record['data'].get('contacts'),
                'address': record['data'].get('address')
            }
    
    if location_specific:
        merged['data']['location_specific_data'] = location_specific
    
    # Merge observed/missing fields
    all_observed = set()
    all_missing = set()
    for record in records:
        all_observed.update(record.get('observed_fields', []))
        all_missing.update(record.get('missing_fields', []))
    
    # Remove from missing if observed anywhere
    all_missing = all_missing - all_observed
    
    merged['observed_fields'] = sorted(list(all_observed))
    merged['missing_fields'] = sorted(list(all_missing))
    
    # Set status based on merged data
    merged['scrape_status'] = 'SUCCESS' if not merged.get('errors') else 'PARTIAL'
    
    return merged


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python transform_organizations.py <input_jsonl> [output_jsonl]")
        sys.exit(1)
    
    input_file = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else input_file.replace('.jsonl', '_transformed.jsonl')
    
    merge_organizations(input_file, output_file)
    print(f"Output written to: {output_file}")
