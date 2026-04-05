-- AIRPORTS
CREATE TABLE public.airports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    icao TEXT UNIQUE,
    iata TEXT,
    name TEXT NOT NULL,
    airport_type TEXT,
    city_id INTEGER REFERENCES public.cities(id),
    country_id INTEGER REFERENCES public.countries(id),
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,
    elevation_ft INTEGER,
    fuel_available TEXT,
    approaches TEXT,
    runway_surface TEXT,
    longest_runway_length_ft INTEGER,
    longest_runway_width_ft INTEGER,
    longest_runway_ident TEXT,
    utc_offset INTERVAL,
    pcn TEXT,
    url TEXT,
    scrape_status TEXT,
    external_id TEXT,
    observed_fields TEXT[],
    missing_fields TEXT[],
    errors JSONB,
    -- raw / operational fields from scraped data
    afs_aftn TEXT,
    airport_general_remarks TEXT,
    airport_hours TEXT,
    airport_light_intensity TEXT,
    airport_manager_phone TEXT,
    airport_email TEXT,
    airport_of_entry TEXT,
    airport_of_entry_remarks TEXT,
    airport_ownership TEXT,
    airport_website TEXT,
    atis_frequency TEXT,
    control_tower_hours TEXT,
    coordinates_raw TEXT,
    ctaf_frequency TEXT,
    customs TEXT,
    distance_from_city TEXT,
    dst TEXT,
    elevation_raw TEXT,
    faa_id TEXT,
    facility_use TEXT,
    fire_category TEXT,
    fire_category_remarks TEXT,
    handling_mandatory TEXT,
    local_standard_time TEXT,
    longest_runway_raw TEXT,
    open_24h TEXT,
    slots_required TEXT,
    sunrise TEXT,
    sunset TEXT,
    tower_frequency TEXT,
    us_customs_pre_clearance TEXT,
    variation TEXT,
    extra JSONB DEFAULT '{}'::jsonb,
    created_by TEXT DEFAULT 'SYSTEM',
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_by TEXT DEFAULT 'SYSTEM',
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- ORGANIZATIONS
CREATE TABLE public.organizations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT UNIQUE NOT NULL,
    description TEXT,
    website TEXT,
    email TEXT,
    phone TEXT,
    distance_from_airport TEXT,
    price_range TEXT,
    sita_code TEXT,
    aftn_code TEXT,
    brand TEXT,
    frequency TEXT,
    phone_after_hours TEXT,
    fax TEXT,
    postal_code TEXT,
    label TEXT,
    url TEXT,
    scrape_status TEXT,
    external_id TEXT,
    observed_fields TEXT[],
    missing_fields TEXT[],
    contacts JSONB,
    associated_airports TEXT[],
    roles TEXT[],
    errors JSONB,
    extra JSONB DEFAULT '{}'::jsonb,
    address_id INTEGER REFERENCES public.addresses(id),
    created_by TEXT DEFAULT 'SYSTEM',
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_by TEXT DEFAULT 'SYSTEM',
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- CLEARANCES
CREATE TABLE public.clearances (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    country TEXT,
    country_phone_code TEXT,
    currency TEXT,
    exchange_guide TEXT,
    time_zone TEXT,
    general_information TEXT,
    wgs84 TEXT,
    visa TEXT,
    documentation TEXT,
    application_format TEXT,
    comments TEXT,
    clearance_contacts JSONB,
    url TEXT,
    scrape_status TEXT,
    external_id TEXT UNIQUE,
    observed_fields TEXT[],
    missing_fields TEXT[],
    errors JSONB,
    associated_airports TEXT[],
    extra JSONB DEFAULT '{}'::jsonb,
    created_by TEXT DEFAULT 'SYSTEM',
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_by TEXT DEFAULT 'SYSTEM',
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- NEARBY AIRPORTS
CREATE TABLE public.nearby_airports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    associated_airports TEXT[],
    icaos TEXT[],
    names TEXT[],
    urls TEXT[],
    primary_runways TEXT[],
    airport_types TEXT[],
    cities TEXT[],
    url TEXT,
    scrape_status TEXT,
    external_id TEXT UNIQUE,
    observed_fields TEXT[],
    missing_fields TEXT[],
    errors JSONB,
    extra JSONB DEFAULT '{}'::jsonb,
    created_by TEXT DEFAULT 'SYSTEM',
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_by TEXT DEFAULT 'SYSTEM',
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- RELATIONSHIP TABLES
CREATE TABLE public.organization_airports (
    organization_id UUID REFERENCES public.organizations(id) ON DELETE CASCADE,
    airport_id UUID REFERENCES public.airports(id) ON DELETE CASCADE,
    PRIMARY KEY (organization_id, airport_id),
    created_by TEXT DEFAULT 'SYSTEM',
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_by TEXT DEFAULT 'SYSTEM',
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE public.airport_clearances (
    airport_id UUID REFERENCES public.airports(id) ON DELETE CASCADE,
    clearance_id UUID REFERENCES public.clearances(id) ON DELETE CASCADE,
    PRIMARY KEY (airport_id, clearance_id),
    created_by TEXT DEFAULT 'SYSTEM',
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_by TEXT DEFAULT 'SYSTEM',
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE public.airport_nearby_airports (
    airport_id UUID REFERENCES public.airports(id) ON DELETE CASCADE,
    nearby_airport_id UUID REFERENCES public.airports(id) ON DELETE CASCADE,
    PRIMARY KEY (airport_id, nearby_airport_id),
    created_by TEXT DEFAULT 'SYSTEM',
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_by TEXT DEFAULT 'SYSTEM',
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- SUPPORTING TABLES
CREATE TABLE public.countries (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    iso2 TEXT,
    iso3 TEXT
);

CREATE TABLE public.cities (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    country_id INTEGER REFERENCES public.countries(id)
);

CREATE TABLE public.addresses (
    id SERIAL PRIMARY KEY,
    city_id INTEGER REFERENCES public.cities(id),
    street VARCHAR(255),
    country_id INTEGER REFERENCES public.countries(id),
    full_address TEXT,
    postal_code VARCHAR(50)
);

CREATE TABLE public.contacts (
    id SERIAL PRIMARY KEY,
    entity_id UUID,
    type TEXT,
    value TEXT,
    label TEXT,
    UNIQUE (entity_id, type, value)
);

CREATE TABLE public.organization_roles (
    id SERIAL PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    created_by TEXT DEFAULT 'SYSTEM',
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_by TEXT DEFAULT 'SYSTEM',
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE public.organization_role_map (
    organization_id UUID REFERENCES public.organizations(id) ON DELETE CASCADE,
    role_id INTEGER REFERENCES public.organization_roles(id),
    PRIMARY KEY (organization_id, role_id),
    created_by TEXT DEFAULT 'SYSTEM',
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_by TEXT DEFAULT 'SYSTEM',
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE public.etl_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    entity_type TEXT,
    records_processed INTEGER,
    records_inserted INTEGER,
    records_updated INTEGER,
    records_failed INTEGER,
    status TEXT CHECK (status IN ('RUNNING', 'SUCCESS', 'FAILED')),
    error_summary TEXT
);

CREATE TABLE public.app_logs (
    id BIGSERIAL PRIMARY KEY,
    level TEXT CHECK (level IN ('INFO', 'WARN', 'ERROR')),
    message TEXT,
    context JSONB,
    user_id UUID,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE public.scraped_records (
    id BIGSERIAL PRIMARY KEY,
    entity_type TEXT NOT NULL,
    external_id TEXT NOT NULL,
    url TEXT,
    scrape_status TEXT CHECK (scrape_status IN ('SUCCESS', 'PARTIAL', 'FAILED')) NOT NULL,
    data JSONB NOT NULL,
    observed_fields TEXT[],
    missing_fields TEXT[],
    errors JSONB,
    validation_errors TEXT[],
    raw_hash TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_raw_entity ON public.scraped_records (entity_type);
CREATE INDEX idx_raw_external_id ON public.scraped_records (external_id);
CREATE INDEX idx_raw_data_gin ON public.scraped_records USING GIN (data);
