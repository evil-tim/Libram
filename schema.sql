-- schema for a financial data scraping and storage system
-- target: postgres 18
------
-- datasource
-- Stores information about a source for financial data, such as a website or API.
-- Defines which specific implementation to use to scrape or retrieve data from that
-- source, and any necessary configuration parameters specific to that source and implementation.
CREATE TABLE IF NOT EXISTS datasource (
    id uuid PRIMARY KEY DEFAULT uuidv4(),
    name text NOT NULL,
    implementation text NOT NULL,
    config jsonb NOT NULL DEFAULT '{}'::jsonb
);
-- entity
-- Represents a financial entity, such as a stock, cryptocurrency, or commodity.
-- Each entity is associated with a datasource, which defines how to retrieve data for that entity.
-- Contains a code and name for the entity, as well as any necessary configuration parameters
-- specific to that entity and datasource.
CREATE TABLE IF NOT EXISTS entity (
    id uuid PRIMARY KEY DEFAULT uuidv4(),
    code text NOT NULL,
    name text NOT NULL,
    -- entity can be denominated in a specific currency, which can be used to convert prices to a common currency for comparison
    -- optional when the entity is a currency itself
    currency_id uuid REFERENCES entity(id),
    datasource_id uuid NOT NULL REFERENCES datasource(id),
    config jsonb NOT NULL DEFAULT '{}'::jsonb,
    type text NOT NULL, -- e.g. stock, crypto, commodity, currency, etc.
    frequency text NOT NULL, -- e.g. daily, hourly, etc.
    has_weekend boolean NOT NULL DEFAULT false, -- whether the entity has price data on weekends (e.g. crypto vs stock)
    timezone text NOT NULL, -- timezone for the entity's price data
    min_timestamp timestamptz, -- earliest timestamp for which price data is available for this entity
    -- we obtain data for an entity from different datasources
    UNIQUE (datasource_id, code)
);
-- price
-- Stores price data for a financial entity at a specific timestamp.
-- Each price record is associated with an entity, and contains the price and/or
-- open, high, low, and close values for that entity at the given timestamp.
CREATE TABLE IF NOT EXISTS price (
    id uuid PRIMARY KEY DEFAULT uuidv4(),
    entity_id uuid NOT NULL REFERENCES entity(id),
    -- single price for a specific timestamp for data measured at that timestamp
    price numeric,
    timestamp timestamptz,
    --
    -- open, high, low, close values for data measured over a specific time period
    open numeric,
    high numeric,
    low numeric,
    close numeric,
    timestamp_start timestamptz,
    timestamp_end timestamptz,
    --
    created_at timestamptz NOT NULL DEFAULT now(),
    -- must provide either a single-timestamp price or full OHLC
    CHECK (
        (
            price IS NOT NULL
            AND timestamp IS NOT NULL
        )
        OR (
            open IS NOT NULL
            AND high IS NOT NULL
            AND low IS NOT NULL
            AND close IS NOT NULL
            AND timestamp_start IS NOT NULL
            AND timestamp_end IS NOT NULL
        )
    ),
    -- ensure interval bounds make sense when present
    CHECK (
        timestamp_start IS NULL
        OR timestamp_end IS NULL
        OR timestamp_start < timestamp_end
    )
);
CREATE INDEX IF NOT EXISTS idx_price_entity_timestamp ON price (entity_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_price_entity_interval ON price (entity_id, timestamp_start, timestamp_end);
-- task
-- Represents a scheduled task to fetch price data for a specific entity over a defined time range.
-- Each task is associated with an entity, and contains the start and end timestamps for the data to be fetched.
CREATE TABLE IF NOT EXISTS task (
    id uuid PRIMARY KEY DEFAULT uuidv4(),
    entity_id uuid NOT NULL REFERENCES entity(id),
    timestamp_start timestamptz NOT NULL,
    timestamp_end timestamptz NOT NULL,
    status text NOT NULL DEFAULT 'OPEN',
    retry_count integer NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_task_entity_start_end ON task (entity_id, timestamp_start, timestamp_end);
