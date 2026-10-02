-- Airports are looked up by ICAO or by AC-U-KWIK source ID (code-less airports);
-- without this index every such lookup scans the whole airports table.
CREATE INDEX IF NOT EXISTS idx_airports_source_airport_id
    ON public.airports (source_airport_id)
    WHERE source_airport_id IS NOT NULL;
