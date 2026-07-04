# Phase 0 Data Capability Report

## Execution

- Timestamp: 2026-07-04T01:51:48.898327+00:00
- Operating system: Darwin 25.5.0
- Python: 3.13.9
- Package: vnstock 4.0.4
- Authentication: VNSTOCK_API_KEY set

## Documentation Sources

- https://vnstocks.com/docs
- https://vnstocks.com/docs/vnstock/du-lieu-thi-truong-market-data
- https://vnstocks.com/docs/vnstock/tra-cuu-thong-tin-tham-chieu-reference
- https://vnstocks.com/docs/vnstock/so-sanh-free-va-sponsor
- https://vnstocks.com/docs/tai-lieu/lich-su-phien-ban
- https://github.com/vnstock-hq/vnstock-agent-guide/blob/main/AGENTS.md

## Documented Free-Tier Constraints

- Community/free users are directed to the vnstock package only.
- Official comparison states community rate limits are very low for automated systems.
- Local package startup message observed community tier as 60 requests per minute.
- Sponsor documentation advertises higher limits and deeper data for automated workflows.
- Documented or observed rate limit: Community package startup message observed 60 requests/minute; official comparison page describes free rate limits as very low but does not provide a full quota table.

## Capability Summary

- VERIFIED: 5
- DOCUMENTED_NOT_TESTED: 2
- UNAVAILABLE_FREE_TIER: 3
- UNAVAILABLE_PACKAGE: 1
- EMPTY_RESPONSE: 1
- UNKNOWN: 2

## Detailed Findings

### daily historical OHLCV

- Status: VERIFIED
- Method or endpoint: Market().equity('FPT').ohlcv(..., resolution='1D')
- Tested symbols: FPT
- Returned rows: 33
- Latency: 1965.54 ms
- Earliest timestamp: 2026-05-20T07:00:00
- Latest timestamp: 2026-07-03T07:00:00
- Timezone: naive/unspecified
- Error category: NONE
- Schema: time: datetime64[ns], open: float64, high: float64, low: float64, close: float64, volume: int64
- Evidence: Live request completed in the local environment.

### historical intraday bars

- Status: VERIFIED
- Method or endpoint: Market().equity('FPT').ohlcv(..., resolution='1m')
- Tested symbols: FPT
- Returned rows: 5
- Latency: 195.63 ms
- Earliest timestamp: 2026-07-03T14:26:00
- Latest timestamp: 2026-07-03T14:40:00
- Timezone: naive/unspecified
- Error category: NONE
- Schema: time: datetime64[ns], open: float64, high: float64, low: float64, close: float64, volume: int64
- Evidence: Live request completed in the local environment.

### latest quote

- Status: VERIFIED
- Method or endpoint: Market().equity('VCB').quote()
- Tested symbols: VCB
- Returned rows: 1
- Latency: 133.5 ms
- Earliest timestamp: 1970-01-01T00:29:43.067546136
- Latest timestamp: 1970-01-01T00:29:43.067546136
- Timezone: naive/unspecified
- Error category: NONE
- Schema: symbol: object, time: int64, exchange: object, ceiling_price: int64, floor_price: int64, reference_price: int64, open_price: int64, high_price: int64, low_price: int64, close_price: int64, average_price: int64, volume_accumulated: int64, total_value: int64, price_change: int64, percent_change: float64, bid_price_1: object, bid_vol_1: int64, bid_price_2: int64, bid_vol_2: int64, bid_price_3: int64, bid_vol_3: int64, ask_price_1: object, ask_vol_1: int64, ask_price_2: int64, ask_vol_2: int64, ask_price_3: int64, ask_vol_3: int64, foreign_buy_volume: int64, foreign_sell_volume: int64, foreign_room: int64
- Evidence: Live request completed in the local environment.

### batch price board

- Status: VERIFIED
- Method or endpoint: Market().quote(['VCB', 'HPG', 'FPT'])
- Tested symbols: VCB, HPG, FPT
- Returned rows: 3
- Latency: 107.95 ms
- Earliest timestamp: 1970-01-01T00:29:43.067546136
- Latest timestamp: 1970-01-01T00:29:43.067591754
- Timezone: naive/unspecified
- Error category: NONE
- Schema: symbol: object, time: int64, exchange: object, ceiling_price: int64, floor_price: int64, reference_price: int64, open_price: int64, high_price: int64, low_price: int64, close_price: int64, average_price: int64, volume_accumulated: int64, total_value: int64, price_change: int64, percent_change: float64, bid_price_1: object, bid_vol_1: int64, bid_price_2: int64, bid_vol_2: int64, bid_price_3: int64, bid_vol_3: int64, ask_price_1: object, ask_vol_1: int64, ask_price_2: int64, ask_vol_2: int64, ask_price_3: int64, ask_vol_3: int64, foreign_buy_volume: int64, foreign_sell_volume: int64, foreign_room: int64
- Data quality findings: Timestamps in 'time' are not sorted ascending.
- Evidence: Live request completed in the local environment.

### symbol metadata

- Status: DOCUMENTED_NOT_TESTED
- Method or endpoint: Reference().equity.list() / Reference().company(symbol).info()
- Tested symbols: FPT, HPG, VCB
- Returned rows: not tested
- Latency: not tested ms
- Earliest timestamp: not available
- Latest timestamp: not available
- Timezone: not available
- Error category: NONE
- Evidence: Official Reference docs document listed equity and company info methods.

### current exchange universe

- Status: VERIFIED
- Method or endpoint: Reference().equity.list_by_exchange()
- Tested symbols: HOSE
- Returned rows: 3303
- Latency: 342.14 ms
- Earliest timestamp: not available
- Latest timestamp: not available
- Timezone: not available
- Error category: NONE
- Schema: symbol: object, organ_name: object, en_organ_name: object, exchange: object, type: object, id: int64
- Data quality findings: No recognized timestamp column was present.; Null values detected in columns: organ_name, en_organ_name, exchange.
- Evidence: Live request completed in the local environment.

### listing date and delisting status

- Status: UNKNOWN
- Method or endpoint: Reference().company(symbol).info()
- Tested symbols: FPT
- Returned rows: not tested
- Latency: not tested ms
- Earliest timestamp: not available
- Latest timestamp: not available
- Timezone: not available
- Error category: NONE
- Evidence: Company info is documented, but listing/delisting fields were not verified.

### corporate actions

- Status: EMPTY_RESPONSE
- Method or endpoint: Reference().company('FPT').events()
- Tested symbols: FPT
- Returned rows: 0
- Latency: 155.14 ms
- Earliest timestamp: not available
- Latest timestamp: not available
- Timezone: not available
- Error category: EMPTY_RESPONSE
- Data quality findings: Response contained no rows.
- Evidence: Live request completed in the local environment.

### adjusted prices

- Status: UNKNOWN
- Method or endpoint: Market().equity(symbol).ohlcv()
- Tested symbols: FPT
- Returned rows: not tested
- Latency: not tested ms
- Earliest timestamp: not available
- Latest timestamp: not available
- Timezone: not available
- Error category: NONE
- Evidence: Adjusted versus unadjusted price semantics were not verified in docs or live data.

### foreign trading

- Status: UNAVAILABLE_FREE_TIER
- Method or endpoint: not verified
- Tested symbols: none
- Returned rows: not tested
- Latency: not tested ms
- Earliest timestamp: not available
- Latest timestamp: not available
- Timezone: not available
- Error category: NONE
- Evidence: Official free-vs-sponsor comparison lists foreign_flow as missing from community Market.equity.

### proprietary trading

- Status: UNAVAILABLE_FREE_TIER
- Method or endpoint: not verified
- Tested symbols: none
- Returned rows: not tested
- Latency: not tested ms
- Earliest timestamp: not available
- Latest timestamp: not available
- Timezone: not available
- Error category: NONE
- Evidence: Official free-vs-sponsor comparison lists proprietary_flow as missing from community Market.equity.

### order-book depth

- Status: UNAVAILABLE_FREE_TIER
- Method or endpoint: not verified
- Tested symbols: none
- Returned rows: not tested
- Latency: not tested ms
- Earliest timestamp: not available
- Latest timestamp: not available
- Timezone: not available
- Error category: NONE
- Evidence: Official free-vs-sponsor comparison lists order_book as missing from community Market.equity.; Version history notes price_depth removal from free VCI quote.

### streaming or WebSocket

- Status: UNAVAILABLE_PACKAGE
- Method or endpoint: not verified
- Tested symbols: none
- Returned rows: not tested
- Latency: not tested ms
- Earliest timestamp: not available
- Latest timestamp: not available
- Timezone: not available
- Error category: NONE
- Evidence: No documented WebSocket interface was found in the inspected free-package docs.; Agent Guide lists production pipeline/streaming under sponsored libraries.

### rate limits

- Status: DOCUMENTED_NOT_TESTED
- Method or endpoint: not verified
- Tested symbols: none
- Returned rows: not tested
- Latency: not tested ms
- Earliest timestamp: not available
- Latest timestamp: not available
- Timezone: not available
- Error category: NONE
- Limitations: No live response headers were observed in this run.
- Evidence: Local package startup text observed community tier as 60 requests per minute.; Official comparison page states community rate limits are very low for automated systems.

## Schema And Timestamp Findings

Schema and timestamp findings are listed per capability above. Live schema validation remains pending for documented-only capabilities.

## Latency Observations

No latency observations are available unless a live audit has been executed.

## Data-Quality Problems

- batch price board: Timestamps in 'time' are not sorted ascending.
- current exchange universe: No recognized timestamp column was present.; Null values detected in columns: organ_name, en_organ_name, exchange.
- corporate actions: Response contained no rows.

## Rate-Limit Implications

- 20 symbols: reasonable initial ceiling for live polling until latency and quota are verified.
- 50 symbols: requires batch quote evidence and caching before use.
- 100 symbols: not recommended on the free tier without measured headroom.
- Full HOSE universe: likely requires paid or alternative data for automated polling.

## Unresolved Uncertainties

- No live API-key-backed requests were completed in this Phase 0 run.
- Historical point-in-time universe membership was not verified.
- Adjusted-price methodology and corporate-action completeness were not verified.
- Free-tier minute lookback, pagination behavior, and delay characteristics were not verified.
- WebSocket entitlement for the free tier was not found in the free-package docs inspected.

## Blocking Issues

- None recorded.

## Conclusions

- daily_ohlcv_usable: Documented and package-exposed; require live schema validation before production use.
- historical_minute_data_usable: Documented at 1m resolution, but free-tier lookback/delay/pagination remain unverified.
- free_tier_minute_polling_practical: Unverified; assume not practical beyond a very small universe until live rate/latency evidence exists.
- batch_price_board_polling_practical: Documented for multiple symbols; maximum batch size and latency are unverified.
- websocket_free_tier_available: No free-tier WebSocket interface was verified.
- recommended_maximum_initial_live_universe: 20 symbols until live audit proves latency and rate-limit headroom.
- recommended_polling_frequency: At most once per minute for initial experiments; reduce if rate-limit signals appear.
- alternative_or_paid_source_needed: Likely needed if Phase 1 requires robust minute histories, streaming, order book, or point-in-time corporate-action data.

## Recommended Data Architecture For Phase 1

- Run the live audit with VNSTOCK_API_KEY in a local shell and commit refreshed reports if checks pass.
- Design immutable raw-data storage and normalized point-in-time schemas.
- Define conservative polling limits for a small initial HOSE universe after live latency/rate evidence.
- Decide whether paid or alternative data is needed for minute bars, point-in-time universe data, and corporate actions.
