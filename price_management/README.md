# Price Manager Client

Small Python library that fetches financial price data via pluggable datasource
implementations, stores it into a Postgres database (schema provided in
`schema.sql`) and queries stored price data.

Usage summary

- Construct `PriceManagerClient` with a Postgres DSN.
- Call `fetch_and_store(entity_id, entity_code, start, end)` to fetch an entity's
  prices from start to end timestamp. Specify the entity by its id or code.
- Call `query_entities(entity_id, entity_code, entity_name, freqency)` to return stored
  entities optionally filtered by id, code, name or frequency
- Call `query_prices(entity_id, start, end)` to return stored prices.

Datasource implementations

Implementations must subclass `libram.datasource.BaseDatasource` and provide
`fetch_prices(entity, start, end)` which yields `libram.types.PriceRecord`.

The `datasource.implementation` column should contain a Python import path in
the form `module.path:ClassName` (or just `module.path` to use `Datasource`).

Example

```py
client = PriceManagerClient("postgresql://user:pass@localhost:5432/mydb")
entities = client.query_entities()
inserted = client.fetch_and_store("__UUID__", "BTCUSD", datetime(2023,1,1), datetime(2023,1,31))
print("inserted", inserted)
prices = client.query_prices("__UUID__", "BTCUSD", datetime(2023,1,1), datetime(2023,1,31))
```
