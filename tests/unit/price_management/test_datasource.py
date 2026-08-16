# ruff: noqa: DTZ005

from datetime import datetime

from price_management.datasource import BaseDatasource


class ConcreteDatasource(BaseDatasource):
    def fetch_prices(self, entity, start, end):
        return []


def test_base_datasource_defaults_missing_config_to_empty_dict():
    assert ConcreteDatasource({"token": "secret"}).config == {"token": "secret"}
    assert ConcreteDatasource({}).config == {}


def test_concrete_datasource_contract_returns_iterable():
    assert (
        list(ConcreteDatasource({}).fetch_prices({}, datetime.now(), datetime.now()))
        == []
    )
