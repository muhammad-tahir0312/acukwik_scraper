"""
Location lookup utilities: match against normalized countries, states, and cities
Do NOT insert new normalized rows. Return None when no confident match.
"""
from etl.db import get_db_cursor

# Simple in-process cache to reduce DB hits during a run
_cache = {
    'countries': {},
    'states': {},
    'cities': {}
}

# Small, curated alias map for common transliteration/typo variants we see in scraped data.
# Map variant -> canonical form that should exist in the normalized tables.
_ALIASES = {
    'cities': {
        'lenkoran': 'lankaran',
        'gyandzha': 'ganja',
        'gabala': 'qabala',
        'zagatala': 'zaqatala',
        "moulavi bazar": 'moulvibazar',
        'viciebsk': 'vitebsk',
        'mogilev': 'mahilyow',
    },
    'states': {
        # add if needed later
    },
    'countries': {
        # country-level canonicalization (lowercase) if needed
    }
}

def _normalize_token(s):
    if not s:
        return None
    s = s.strip()
    # remove parentheses content
    import re
    s = re.sub(r"\(.*?\)", "", s)
    # collapse whitespace and trailing commas
    s = re.sub(r"\s+", " ", s).strip().strip(',')
    return s

def _split_candidates(raw):
    if not raw:
        return []
    # split on comma or slash and return tokens
    parts = []
    for sep in [',', '/']:
        if sep in raw:
            parts = [p.strip() for p in raw.split(sep) if p.strip()]
            break
    if not parts:
        parts = [raw.strip()]
    return parts

def match_country(raw_name, cur=None):
    """Return (country_id, candidates_list) or (None, [])."""
    name = _normalize_token(raw_name)
    if not name:
        return None, []
    if name in _cache['countries']:
        return _cache['countries'][name], []

    close_candidates = []
    # If a cursor not provided, open one
    if not cur:
        with get_db_cursor() as cur_ctx:
            return match_country(name, cur_ctx)

    # Try iso2 / iso3 exact
    cur.execute("SELECT id, name, iso2, iso3 FROM countries WHERE LOWER(iso2)=LOWER(%s) OR LOWER(iso3)=LOWER(%s)", [name, name])
    row = cur.fetchone()
    if row:
        _cache['countries'][name] = row['id']
        return row['id'], []

    # Try exact ILIKE match on full name
    cur.execute("SELECT id, name FROM countries WHERE name ILIKE %s", [name])
    rows = cur.fetchall()
    if len(rows) == 1:
        _cache['countries'][name] = rows[0]['id']
        return rows[0]['id'], []
    elif len(rows) > 1:
        candidates = [{'id': r['id'], 'name': r['name']} for r in rows]
        return None, candidates

    # Tokenize and try tokens (e.g., "State, Country")
    for token in _split_candidates(name):
        cur.execute("SELECT id, name FROM countries WHERE name ILIKE %s", [token])
        rows = cur.fetchall()
        if len(rows) == 1:
            _cache['countries'][name] = rows[0]['id']
            return rows[0]['id'], []
        elif len(rows) > 1:
            candidates = [{'id': r['id'], 'name': r['name']} for r in rows]
            close_candidates.extend(candidates)

    # Try common variant: strip leading 'the ' (e.g., 'The Bahamas')
    if name.lower().startswith('the '):
        stripped = name[4:]
        cur.execute("SELECT id, name FROM countries WHERE name ILIKE %s", [stripped])
        rows = cur.fetchall()
        if len(rows) == 1:
            _cache['countries'][name] = rows[0]['id']
            return rows[0]['id'], []
        elif len(rows) > 1:
            candidates = [{'id': r['id'], 'name': r['name']} for r in rows]
            close_candidates.extend(candidates)

    # As a last resort, try contained match (e.g. '%Bahamas%') but only accept a single unique result
    cur.execute("SELECT id, name FROM countries WHERE name ILIKE %s", [f"%{name}%"])
    rows = cur.fetchall()
    uniq_ids = {r['id'] for r in rows}
    if len(rows) == 1 or len(uniq_ids) == 1 and len(rows) >= 1:
        # if contains yields a single unique id, accept it
        chosen = rows[0]
        _cache['countries'][name] = chosen['id']
        return chosen['id'], []
    elif len(rows) > 1:
        candidates = [{'id': r['id'], 'name': r['name']} for r in rows]
        close_candidates.extend(candidates)

    # No confident match
    return None, close_candidates

def match_state(raw_name, country_id=None, cur=None):
    """Return (state_id, candidates_list) or (None, [])."""
    name = _normalize_token(raw_name)
    if not name:
        return None, []
    # apply alias mapping for states
    key = name.lower()
    if key in _ALIASES.get('states', {}):
        name = _ALIASES['states'][key]
    cache_key = f"{name}||{country_id}"
    if cache_key in _cache['states']:
        return _cache['states'][cache_key], []
    if not cur:
        with get_db_cursor() as cur_ctx:
            return match_state(name, country_id, cur_ctx)
    params = [name]
    sql = "SELECT id, name, country_id FROM states WHERE name ILIKE %s"
    if country_id:
        sql += " AND country_id = %s"
        params.append(country_id)
    cur.execute(sql, params)
    rows = cur.fetchall()
    if len(rows) == 1:
        _cache['states'][cache_key] = rows[0]['id']
        return rows[0]['id'], []
    elif len(rows) > 1:
        candidates = [{'id': r['id'], 'name': r['name'], 'country_id': r['country_id']} for r in rows]
        return None, candidates
    # try tokens
    for token in _split_candidates(name):
        params = [token]
        sql = "SELECT id, name, country_id FROM states WHERE name ILIKE %s"
        if country_id:
            sql += " AND country_id = %s"
            params.append(country_id)
        cur.execute(sql, params)
        rows = cur.fetchall()
        if len(rows) == 1:
            _cache['states'][cache_key] = rows[0]['id']
            return rows[0]['id'], []
        elif len(rows) > 1:
            candidates = [{'id': r['id'], 'name': r['name'], 'country_id': r['country_id']} for r in rows]
            return None, candidates
    return None, []

def match_city(raw_name, country_id=None, state_id=None, cur=None):
    """Return (city_id, candidates_list) or (None, [])."""
    name = _normalize_token(raw_name)
    if not name:
        return None, []
    # apply alias mapping for cities
    key = name.lower()
    if key in _ALIASES.get('cities', {}):
        name = _ALIASES['cities'][key]
    cache_key = f"{name}||{country_id}||{state_id}"
    if cache_key in _cache['cities']:
        return _cache['cities'][cache_key], []
    if not cur:
        with get_db_cursor() as cur_ctx:
            return match_city(name, country_id, state_id, cur_ctx)
    params = [name]
    sql = "SELECT id, name, country_id FROM cities WHERE name ILIKE %s"
    if country_id:
        sql += " AND country_id = %s"
        params.append(country_id)
    cur.execute(sql, params)
    rows = cur.fetchall()
    if len(rows) == 1:
        _cache['cities'][cache_key] = rows[0]['id']
        return rows[0]['id'], []
    elif len(rows) > 1:
        # if state_id provided try to filter by state via cities->country only available
        candidates = [{'id': r['id'], 'name': r['name'], 'country_id': r['country_id']} for r in rows]
        return None, candidates
    # try tokens
    for token in _split_candidates(name):
        params = [token]
        sql = "SELECT id, name, country_id FROM cities WHERE name ILIKE %s"
        if country_id:
            sql += " AND country_id = %s"
            params.append(country_id)
        cur.execute(sql, params)
        rows = cur.fetchall()
        if len(rows) == 1:
            _cache['cities'][cache_key] = rows[0]['id']
            return rows[0]['id'], []
        elif len(rows) > 1:
            candidates = [{'id': r['id'], 'name': r['name'], 'country_id': r['country_id']} for r in rows]
            return None, candidates
    return None, []