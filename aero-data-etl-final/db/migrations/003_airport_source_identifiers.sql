BEGIN;

ALTER TABLE public.airports
    ADD COLUMN IF NOT EXISTS source_airport_id TEXT;

UPDATE public.airports
SET source_airport_id = substring(url FROM '/Airport-Info/([^/?#]+)')
WHERE source_airport_id IS NULL
  AND url IS NOT NULL;

UPDATE public.airports
SET external_id = CASE
    WHEN icao IS NOT NULL THEN 'acukwik_' || icao
    WHEN faa_id IS NOT NULL THEN 'acukwik_faa_' || faa_id
    WHEN iata IS NOT NULL THEN 'acukwik_iata_' || iata
    WHEN source_airport_id IS NOT NULL THEN 'acukwik_source_' || source_airport_id
    ELSE external_id
END
WHERE external_id IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_airports_external_id_uniq
    ON public.airports (external_id)
    WHERE external_id IS NOT NULL;

COMMENT ON COLUMN public.airports.source_airport_id IS
    'Stable AC-U-KWIK Airport-Info path identifier; used when ICAO/IATA/FAA is absent.';

COMMIT;
