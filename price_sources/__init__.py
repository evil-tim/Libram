"""Package exposing available price datasource implementations for discoverability.

Importing classes here makes it convenient to reference datasources from other
modules (e.g. configuration or registry code) without importing individual files.
"""

from .rest_datasource import RestJSONDatasource
from .html_datasource import HTMLDatasource
from .pse_edge_datasource import PSEEdgeDataSource
from .coindesk_ohlc_datasource import CoindeskOHLCDataSource
from .ofx_forex_datasource import OFXForexDataSource
from .bpi_fund_datasource import BPIFundDataSource
from .manulife_fund_datasource import ManulifeFundDataSource
from .slamc_fund_datasource import SLAMCFundDataSource

__all__ = [
	"RestJSONDatasource",
	"HTMLDatasource",
	"PSEEdgeDataSource",
	"CoindeskOHLCDataSource",
	"OFXForexDataSource",
	"BPIFundDataSource",
	"ManulifeFundDataSource",
	"SLAMCFundDataSource",
]
