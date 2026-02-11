"""
Data validation module.
Validates and normalizes scraped records before output.
"""
import re
import logging
from typing import Dict, Any, List, Optional
import jsonschema

logger = logging.getLogger(__name__)


# JSON Schema definitions for each entity type
VALIDATION_SCHEMAS = {
    "airport": {
        "type": "object",
        "properties": {
            "icao": {"type": ["string", "null"], "pattern": "^[A-Z]{4}$"},
            "iata": {"type": ["string", "null"], "pattern": "^[A-Z]{3}$"},
            "name": {"type": "string", "minLength": 1},
            "city": {"type": "string"},
            "country": {"type": "string"},
            "latitude": {"type": ["number", "null"]},
            "longitude": {"type": ["number", "null"]},
            "elevation": {"type": ["integer", "null"]},
            "timezone": {"type": ["string", "null"]},
            "runways": {"type": "array"},
            "frequencies": {"type": "array"}
        },
        "required": ["name"]
    },
    "organization": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "minLength": 1},
            "description": {"type": "string"},
            "website": {"type": ["string", "null"]},
            "email": {"type": ["string", "null"]},
            "phone": {"type": ["string", "null"]},
            "services": {"type": "array", "items": {"type": "string"}},
            "roles": {"type": "array", "items": {"type": "string"}},
            "associated_airports": {"type": "array", "items": {"type": "string"}},
            "address": {"type": ["string", "null"]},
            "hours": {"type": ["string", "null"]}
        },
        "required": ["name"]
    }
}


def validate_record(record: Dict[str, Any]) -> List[str]:
    """
    Validate a scraped record against its schema.
    
    Args:
        record: Scraped record to validate
        
    Returns:
        List of validation error messages (empty if valid)
    """
    errors = []
    
    try:
        # Check required top-level fields
        required_fields = ["source", "entity_type", "external_id", "scraped_at", "data"]
        for field in required_fields:
            if field not in record:
                errors.append(f"Missing required field: {field}")
        
        if errors:
            return errors
        
        # Validate entity type
        entity_type = record["entity_type"]
        if entity_type not in VALIDATION_SCHEMAS:
            errors.append(f"Unknown entity type: {entity_type}")
            return errors
        
        # Validate data against schema
        schema = VALIDATION_SCHEMAS[entity_type]
        try:
            jsonschema.validate(instance=record["data"], schema=schema)
        except jsonschema.ValidationError as e:
            errors.append(f"Schema validation failed: {e.message}")
        except jsonschema.SchemaError as e:
            logger.error(f"Invalid schema: {e}")
            errors.append(f"Internal schema error: {e.message}")
        
        # Additional custom validations
        data = record["data"]
        
        if entity_type == "airport":
            errors.extend(_validate_airport_data(data))
        elif entity_type == "organization":
            errors.extend(_validate_organization_data(data))
        
        # Validate scraped_at timestamp format
        if not _is_valid_iso8601(record["scraped_at"]):
            errors.append(f"Invalid timestamp format: {record['scraped_at']}")
        
    except Exception as e:
        logger.error(f"Validation error: {e}")
        errors.append(f"Validation exception: {str(e)}")
    
    return errors


def _validate_airport_data(data: Dict[str, Any]) -> List[str]:
    """Validate airport-specific data."""
    errors = []
    
    # ICAO code validation
    icao = data.get("icao")
    if icao:
        if not re.match(r'^[A-Z]{4}$', icao):
            errors.append(f"Invalid ICAO format: {icao} (must be 4 uppercase letters)")
    else:
        errors.append("Missing ICAO code")
    
    # IATA code validation (optional but must be valid if present)
    iata = data.get("iata")
    if iata and not re.match(r'^[A-Z]{3}$', iata):
        errors.append(f"Invalid IATA format: {iata} (must be 3 uppercase letters)")
    
    # Coordinate validation
    lat = data.get("latitude")
    if lat is not None:
        if not -90 <= lat <= 90:
            errors.append(f"Invalid latitude: {lat} (must be between -90 and 90)")
    
    lon = data.get("longitude")
    if lon is not None:
        if not -180 <= lon <= 180:
            errors.append(f"Invalid longitude: {lon} (must be between -180 and 180)")
    
    # Elevation validation
    elevation = data.get("elevation")
    if elevation is not None:
        if elevation < -1500 or elevation > 30000:
            errors.append(f"Suspicious elevation: {elevation} feet")
    
    return errors


def _validate_organization_data(data: Dict[str, Any]) -> List[str]:
    """Validate organization-specific data."""
    errors = []
    
    # Email validation
    email = data.get("email")
    if email and not _is_valid_email(email):
        errors.append(f"Invalid email format: {email}")
    
    # Website validation
    website = data.get("website")
    if website and not _is_valid_url(website):
        errors.append(f"Invalid website URL: {website}")
    
    # Roles validation
    roles = data.get("roles", [])
    if not roles:
        errors.append("No roles specified for organization")
    
    valid_roles = ["FBO", "FUEL", "HANDLER", "HOTEL", "CATERING", "MAINTENANCE", 
                   "CUSTOMS", "ORGANIZATION"]
    for role in roles:
        if role not in valid_roles:
            errors.append(f"Unknown role: {role}")
    
    # Associated airports validation
    airports = data.get("associated_airports", [])
    for airport in airports:
        if not re.match(r'^[A-Z]{4}$', airport):
            errors.append(f"Invalid airport code in associated_airports: {airport}")
    
    return errors


def _is_valid_email(email: str) -> bool:
    """Check if email format is valid."""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, email))


def _is_valid_url(url: str) -> bool:
    """Check if URL format is valid."""
    pattern = r'^https?://[^\s/$.?#].[^\s]*$'
    return bool(re.match(pattern, url, re.IGNORECASE))


def _is_valid_iso8601(timestamp: str) -> bool:
    """Check if timestamp is in ISO 8601 format."""
    pattern = r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z?$'
    return bool(re.match(pattern, timestamp))


def normalize_icao(icao: Optional[str]) -> Optional[str]:
    """
    Normalize ICAO code to uppercase.
    
    Args:
        icao: Raw ICAO code
        
    Returns:
        Normalized ICAO code or None
    """
    if not icao or icao.upper() in ['N/A', 'NONE', 'NULL']:
        return None
    
    normalized = icao.strip().upper()
    
    # Validate format
    if len(normalized) == 4 and normalized.isalnum():
        return normalized
    
    logger.warning(f"ICAO code doesn't match expected format: {icao}")
    return normalized  # Return anyway but logged warning


def normalize_iata(iata: Optional[str]) -> Optional[str]:
    """
    Normalize IATA code to uppercase.
    
    Args:
        iata: Raw IATA code
        
    Returns:
        Normalized IATA code or None
    """
    if not iata or iata.upper() in ['N/A', 'NONE', 'NULL']:
        return None
    
    normalized = iata.strip().upper()
    
    # Validate format
    if len(normalized) == 3 and normalized.isalpha():
        return normalized
    
    logger.warning(f"IATA code doesn't match expected format: {iata}")
    return normalized


def normalize_email(email: Optional[str]) -> Optional[str]:
    """
    Normalize email to lowercase.
    
    Args:
        email: Raw email address
        
    Returns:
        Normalized email or None
    """
    if not email:
        return None
    
    normalized = email.strip().lower()
    
    # Basic validation
    if '@' not in normalized or '.' not in normalized:
        logger.warning(f"Email may be invalid: {email}")
    
    return normalized


def clean_html(text: Optional[str]) -> Optional[str]:
    """
    Remove HTML tags and extra whitespace from text.
    
    Args:
        text: Raw text possibly containing HTML
        
    Returns:
        Cleaned text or None
    """
    if not text:
        return None
    
    # Remove HTML tags
    text = re.sub(r'<[^>]+>', '', text)
    
    # Remove extra whitespace
    text = re.sub(r'\s+', ' ', text)
    
    # Remove leading/trailing whitespace
    text = text.strip()
    
    return text if text else None


def normalize_phone(phone: Optional[str]) -> Optional[str]:
    """
    Normalize phone number.
    
    Args:
        phone: Raw phone number
        
    Returns:
        Normalized phone or None
    """
    if not phone:
        return None
    
    # Remove common separators but keep the digits and leading +
    normalized = re.sub(r'[^\d+]', '', phone)
    
    # Ensure it has enough digits
    digit_count = len(re.sub(r'\D', '', normalized))
    if digit_count < 7:
        logger.warning(f"Phone number may be invalid: {phone}")
    
    return phone.strip()  # Return original format but stripped


def validate_and_normalize(record: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate and normalize a record in one step.
    
    Args:
        record: Scraped record
        
    Returns:
        Normalized record with validation_errors field added
    """
    # Normalize data fields
    if "data" in record:
        data = record["data"]
        entity_type = record.get("entity_type")
        
        if entity_type == "airport":
            if "icao" in data:
                data["icao"] = normalize_icao(data["icao"])
            if "iata" in data:
                data["iata"] = normalize_iata(data["iata"])
        
        elif entity_type == "organization":
            if "email" in data:
                data["email"] = normalize_email(data["email"])
            if "phone" in data:
                data["phone"] = normalize_phone(data["phone"])
            
            # Normalize associated airports
            if "associated_airports" in data:
                data["associated_airports"] = [
                    normalize_icao(code) 
                    for code in data["associated_airports"]
                    if normalize_icao(code) is not None
                ]
    
    # Validate
    errors = validate_record(record)
    if errors:
        record["validation_errors"] = errors
    
    return record
