"""AC-U-KWIK service-section to canonical role mappings.

Roles describe what an organization does at a particular airport.  They are not
attributes of the organization itself.  Keep this catalog as the single source
of truth for parsing and validation.
"""

from typing import Dict, Iterable, Optional


SECTION_ROLES: Dict[str, str] = {
    "FBOs": "FBO",
    "Handlers": "HANDLER",
    "Supervising Agents": "SUPERVISING_AGENT",
    "Fuel Only": "FUEL_SUPPLIER",
    "Flight Support Organizations": "FLIGHT_SUPPORT_ORGANIZATION",
    "Caterers": "CATERING",
    "Limo": "GROUND_TRANSPORTATION",
    "Maintenance": "MAINTENANCE",
    "Hotels": "HOTEL",
    "Car Rental": "CAR_RENTAL",
    "Charter": "CHARTER",
    "Detailers": "DETAILER",
    "Protection": "PROTECTION",
    "Stores": "STORE",
}


SECTION_ALIASES: Dict[str, str] = {
    "fbo": "FBOs",
    "fbos": "FBOs",
    "handler": "Handlers",
    "handlers": "Handlers",
    "ground handler": "Handlers",
    "ground handlers": "Handlers",
    "supervising agent": "Supervising Agents",
    "supervising agents": "Supervising Agents",
    "fuel": "Fuel Only",
    "fuel only": "Fuel Only",
    "fuel supplier": "Fuel Only",
    "fuel suppliers": "Fuel Only",
    "flight support": "Flight Support Organizations",
    "flight support organization": "Flight Support Organizations",
    "flight support organizations": "Flight Support Organizations",
    "caterer": "Caterers",
    "caterers": "Caterers",
    "catering": "Caterers",
    "limo": "Limo",
    "limousine": "Limo",
    "ground transportation": "Limo",
    "maintenance": "Maintenance",
    "hotel": "Hotels",
    "hotels": "Hotels",
    "car rental": "Car Rental",
    "car rentals": "Car Rental",
    "charter": "Charter",
    "charters": "Charter",
    "detailer": "Detailers",
    "detailers": "Detailers",
    "aircraft detailing": "Detailers",
    "protection": "Protection",
    "security": "Protection",
    "store": "Stores",
    "stores": "Stores",
}


VALID_ROLES = frozenset({*SECTION_ROLES.values(), "OTHER"})


def normalize_section_title(title: str) -> str:
    """Return a stable section name while tolerating counts and punctuation."""
    normalized = " ".join((title or "").replace("&", "and").split()).strip(" :-")
    lowered = normalized.lower()
    for alias, canonical in SECTION_ALIASES.items():
        if lowered == alias or lowered.startswith(f"{alias} ("):
            return canonical
    return normalized


def role_for_section(title: str) -> Optional[str]:
    return SECTION_ROLES.get(normalize_section_title(title))


def all_section_names() -> Iterable[str]:
    return SECTION_ROLES.keys()
