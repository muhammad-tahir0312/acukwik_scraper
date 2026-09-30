ALTER TABLE public.organization_airport_listing_roles
    ADD COLUMN IF NOT EXISTS details JSONB NOT NULL DEFAULT '{}'::jsonb;
