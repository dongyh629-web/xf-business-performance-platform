# v0.9.0

Release date: 2026-07-30

## Added

- Cost Snapshot Engine
- Business Metrics Engine
- Profitability Dashboard
- Data Validation
- Business Sign-off workflow

## Architecture

- Added Cost Snapshot Engine for versioned product cost files.
- Added Business Metrics Engine as the single source of truth for cost, gross profit, margin, and cost coverage metrics.
- Added Profitability Foundation for product, customer, and margin reconciliation views.
- Added Data Validation layer for internal Finance/Admin review before profitability is exposed as an executive view.

## Testing

- Unit tests for Cost Snapshot Engine.
- Unit tests for Business Metrics Engine.
- Unit tests for Data Validation.
- Streamlit AppTest smoke checks for all current app pages.
- Real cached data validation for cost coverage and margin exception review.

## Known Limitations

- Current historical Cost Coverage: 1.76%.
- Historical Cost Snapshots are still required before profitability can be trusted as a boss-facing view.
- Quantity Unit is not available in the source sales data.
- Profitability is currently intended for internal validation only.
- Data Validation and Profitability outputs depend on Product Code matching to cost snapshots.

## Next Version

Planned:

- Sprint 10: Pricing Intelligence
