BEGIN;

DROP INDEX IF EXISTS public.organizations_name_norm_uniq;
CREATE INDEX IF NOT EXISTS organizations_name_norm_idx
    ON public.organizations (lower(trim(name)))
    WHERE name IS NOT NULL;

CREATE TABLE IF NOT EXISTS public.organization_airport_listings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
    airport_id UUID NOT NULL REFERENCES public.airports(id) ON DELETE CASCADE,
    listing_key TEXT NOT NULL,
    source_listing_id TEXT,
    source_profile_url TEXT,
    source_section TEXT NOT NULL,
    source_sections TEXT[] NOT NULL DEFAULT ARRAY[]::text[],
    display_name TEXT NOT NULL,
    display_names TEXT[] NOT NULL DEFAULT ARRAY[]::text[],
    contacts JSONB NOT NULL DEFAULT '[]'::jsonb,
    address JSONB,
    attributes JSONB NOT NULL DEFAULT '{}'::jsonb,
    raw_fields JSONB NOT NULL DEFAULT '[]'::jsonb,
    links JSONB NOT NULL DEFAULT '[]'::jsonb,
    media JSONB NOT NULL DEFAULT '[]'::jsonb,
    raw_text TEXT,
    source_identifiers JSONB NOT NULL DEFAULT '{}'::jsonb,
    url TEXT,
    scrape_status TEXT,
    observed_fields TEXT[],
    missing_fields TEXT[],
    errors JSONB,
    created_by TEXT DEFAULT 'SYSTEM',
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_by TEXT DEFAULT 'SYSTEM',
    updated_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (airport_id, listing_key)
);

ALTER TABLE public.organization_airport_listings
    ADD COLUMN IF NOT EXISTS source_sections TEXT[] NOT NULL DEFAULT ARRAY[]::text[],
    ADD COLUMN IF NOT EXISTS display_names TEXT[] NOT NULL DEFAULT ARRAY[]::text[];

CREATE TABLE IF NOT EXISTS public.organization_airport_listing_roles (
    listing_id UUID REFERENCES public.organization_airport_listings(id) ON DELETE CASCADE,
    role_id INTEGER REFERENCES public.organization_roles(id),
    PRIMARY KEY (listing_id, role_id),
    created_by TEXT DEFAULT 'SYSTEM',
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.organization_airport_roles (
    organization_id UUID REFERENCES public.organizations(id) ON DELETE CASCADE,
    airport_id UUID REFERENCES public.airports(id) ON DELETE CASCADE,
    role_id INTEGER REFERENCES public.organization_roles(id),
    PRIMARY KEY (organization_id, airport_id, role_id),
    created_by TEXT DEFAULT 'SYSTEM',
    created_at TIMESTAMPTZ DEFAULT now()
);

COMMENT ON TABLE public.organization_airport_listings IS
    'Source-of-truth AC-U-KWIK organization listing at one airport.';
COMMENT ON TABLE public.organization_airport_roles IS
    'Roles an organization performs at a specific airport.';
COMMENT ON TABLE public.organization_role_map IS
    'Deprecated aggregate role map retained for backward compatibility.';

COMMIT;
