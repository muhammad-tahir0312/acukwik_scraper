-- Airports are looked up by ICAO or by AC-U-KWIK source ID (code-less airports);
-- without this index every such lookup scans the whole airports table.
-- source_airport_id is the site's own path ID, so it is unique per airport. This
-- matches the portal backend's migration 1709000000026 (same name, UNIQUE).
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE schemaname = 'public' AND indexname = 'idx_airports_source_airport_id'
          AND indexdef NOT LIKE 'CREATE UNIQUE INDEX%'
    ) THEN
        DROP INDEX public.idx_airports_source_airport_id;
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS idx_airports_source_airport_id
    ON public.airports (source_airport_id)
    WHERE source_airport_id IS NOT NULL;
