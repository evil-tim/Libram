"""Package exposing available price datasource implementations for discoverability.

Importing classes here makes it convenient to reference datasources from other
modules (e.g. configuration or registry code) without importing individual files.
"""

from .bpi_fund_datasource import BPIFundDataSource
from .chainlink_datasource import ChainlinkDataSource
from .coindesk_ohlc_datasource import CoindeskOHLCDataSource
from .html_datasource import HTMLDatasource
from .kraken_ticker_datasource import KrakenTickerDataSource
from .manulife_fund_datasource import ManulifeFundDataSource
from .ofx_forex_datasource import OFXForexDataSource
from .pse_edge_datasource import PSEEdgeDataSource
from .rest_datasource import RestJSONDatasource
from .slamc_fund_datasource import SLAMCFundDataSource
from .uniswap_datasource import UniswapDataSource

__all__ = [
	"BPIFundDataSource",
    "ChainlinkDataSource",
	"CoindeskOHLCDataSource",
	"HTMLDatasource",
	"KrakenTickerDataSource",
	"ManulifeFundDataSource",
	"OFXForexDataSource",
	"PSEEdgeDataSource",
	"RestJSONDatasource",
	"SLAMCFundDataSource",
    "UniswapDataSource",
]
