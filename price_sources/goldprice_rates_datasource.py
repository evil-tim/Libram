"""GoldPrice.org spot rate datasource for fetching current XAU/XAG prices.

Extends the REST JSON datasource to call goldprice.org's rate endpoint
(https://data-asg.goldprice.org/dbXRates/{CURRENCY}), the JSON backend behind
the live charts on goldprice.org. See:
https://goldprice.org/

That endpoint returns BOTH metals in a single payload. This datasource is
deliberately bound to ONE entity and selects ONE field from the payload, so
XAU and XAG are two entities backed by two instances of this class.

The reason is that snapshot scheduling is per-entity: `snapshot_state` is keyed
by `entity_id`, and the executor claims, leases, backs off and reschedules one
row at a time. A datasource that wrote two entities from one fetch would have
to hold a lease over rows it was not claimed for, would couple the two
intervals so one could not run without the other, and could not express partial
success (one row of state, one `last_error`, one failure counter). Keeping the
fetch single-entity preserves all of that. The cost is one extra GET against an
unauthenticated endpoint that has not been observed to rate-limit.

Snapshot only: the response carries no time range and no history, so
`fetch_prices` stays unsupported and history is accumulated through
`snapshot_state` runs into the `price` table.

The instance is initialized with a config dict. Expected config keys:
- "url" (required, from the REST base): endpoint URL template. A "{currency}"
  placeholder is substituted from the `currency` key.
- "currency" (required): ISO code the metals are quoted in, e.g. "PHP".
- "field" (required): which price to read from the response item, e.g.
  "xauPrice" or "xagPrice".
- "timestamp_field": optional, defaults to "tsj".

Both `currency` and `field` normally live in the entity's own config so that
one datasource row can back both metals, with entity config taking precedence
over datasource config in the merge done by PriceManagerService.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from libram_types.libram_types import PriceRecord
from price_sources.rest_datasource import RestJSONDatasource

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class GoldPriceRatesDataSource(RestJSONDatasource):
    """Fetch the current spot price of one metal from goldprice.org's rates API."""

    def __init__(self, config: dict):
        super().__init__(config)

        # Specific config keys for the goldprice.org rates datasource
        if not self.config.get("currency"):
            raise ValueError("config must include 'currency'")
        if not self.config.get("field"):
            raise ValueError("config must include 'field'")
        self.currency: str = str(self.config.get("currency"))
        self.field: str = str(self.config.get("field"))
        self.timestamp_field: str = str(self.config.get("timestamp_field", "tsj"))

    def build_request_params_snapshot(
        self,
        entity: dict,
        config: dict,
    ) -> tuple[str | None, dict[str, Any] | None, dict[str, Any] | None]:
        """Return the currency-scoped URL. The endpoint takes no query parameters."""
        return (self.url.replace("{currency}", self.currency), None, None)

    def parse_price_data_snapshot(self, data: dict[str, Any] | list) -> PriceRecord:
        response = self._extract_response(data)

        return PriceRecord(
            price=self._parse_price(self._extract_item(response)),
            timestamp=self._parse_timestamp(response),
        )

    def _extract_response(self, data: dict[str, Any] | list) -> dict[str, Any]:
        """Narrow the parsed JSON to the goldprice.org response object."""
        if not isinstance(data, dict):
            raise TypeError("goldprice.org response was not a JSON object")
        return data

    def _extract_item(self, data: dict[str, Any]) -> dict[str, Any]:
        """Validate the goldprice.org envelope and return its single quote item."""
        items = data.get("items")
        if not isinstance(items, list) or not items:
            raise ValueError("goldprice.org response did not contain any items")
        if len(items) != 1:
            raise ValueError(
                f"Expected exactly one item in goldprice.org response, got {len(items)}"
            )

        item = items[0]
        if not isinstance(item, dict):
            raise TypeError("goldprice.org item was not a JSON object")

        # The payload covers both metals at once, so the only thing tying a
        # response to the entity that requested it is the echoed currency. A
        # mismatch means entity config and the request URL disagree; that would
        # silently write XAU-on-PHP values under an XAU-on-USD entity.
        returned_currency = item.get("curr")
        if returned_currency and str(returned_currency).upper() != self.currency.upper():
            raise ValueError(
                f"goldprice.org returned currency {returned_currency!r} but "
                f"{self.currency!r} was requested"
            )

        return item

    def _parse_price(self, item: dict[str, Any]) -> Decimal:
        """Extract the configured metal price from a quote item."""
        if self.field not in item:
            raise ValueError(
                f"goldprice.org response did not contain field {self.field!r}"
            )

        raw = item[self.field]
        if raw is None:
            raise ValueError(f"goldprice.org field {self.field!r} was null")

        try:
            return Decimal(str(raw))
        except Exception as exc:
            raise ValueError(
                f"goldprice.org field {self.field!r} was not a valid decimal"
            ) from exc

    def _parse_timestamp(self, data: dict[str, Any]) -> datetime:
        """Convert the epoch-millisecond quote timestamp to an aware UTC datetime.

        The payload carries two epoch-millisecond fields, `ts` and `tsj`, which
        disagree by about a minute. `tsj` is the quote time; `ts` is a later
        delivery/cache marker.

        Confirmed against the payload's own human-readable `date` field: for
        ts=1790401019301 / tsj=1790401015574 the response reported
        date="Sep 26th 2026, 01:36:55 am NY", and tsj converts to
        2026-09-26T05:36:55.574Z - exactly 01:36:55 in NY (EDT, UTC-4) - while
        ts would be 01:37:55. Observed responses returning an identical price
        also varied `ts` while holding `tsj` steady, consistent with `ts` being
        per-response rather than per-quote.
        """
        raw = data.get(self.timestamp_field)
        if raw is None:
            raise ValueError(
                f"goldprice.org response did not contain {self.timestamp_field!r}"
            )

        try:
            milliseconds = int(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"goldprice.org {self.timestamp_field!r} was not epoch milliseconds"
            ) from exc

        try:
            # Integer arithmetic rather than fromtimestamp(ms / 1000) so no
            # precision is lost through float before the instant is fixed.
            return _EPOCH + timedelta(milliseconds=milliseconds)
        except (OverflowError, ValueError) as exc:
            raise ValueError(
                f"goldprice.org {self.timestamp_field!r} was out of range"
            ) from exc
