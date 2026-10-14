# Entity Relationship Diagram

Database: PostgreSQL (`meridian`).  
Relationships are logical — `ticker` is the common key across tables but no formal FK constraints are defined in the schema.

```mermaid
erDiagram

    %% ---------------------------------------------------------------
    %% UNIVERSE  (reference / master data)
    %% ---------------------------------------------------------------

    sp500_universe {
        string  ticker                 PK
        string  company_name
        string  gics_sector
        string  gics_sub_industry
        string  updated_at
    }

    benchmark_tickers {
        string  ticker                 PK
        string  category
    }

    %% ---------------------------------------------------------------
    %% LAYER 1 — Market data
    %% ---------------------------------------------------------------

    daily_prices {
        string  ticker                 PK
        string  date                   PK
        float   open
        float   high
        float   low
        float   close
        float   adj_close
        integer volume
    }

    fundamentals {
        string  ticker                 PK
        string  period_type            PK
        string  period_end             PK
        float   revenue
        float   gross_profit
        float   operating_income
        float   ebit
        float   net_income
        float   rd_expense
        float   total_assets
        float   total_liabilities
        float   total_equity
        float   cash
        float   total_debt
        float   current_assets
        float   current_liabilities
        float   accounts_receivable
        float   retained_earnings
        float   shares_outstanding
        float   dividends_paid
        float   cfo
        float   capex
        float   fcf
        float   buybacks
        float   roe
        float   roa
        float   gross_margin
        float   operating_margin
        float   net_margin
        float   revenue_growth_yoy
        float   revenue_growth_qoq
        float   earnings_growth_yoy
        float   earnings_growth_qoq
        float   debt_to_equity
        float   current_ratio
        float   ar_to_revenue
        float   cfo_to_ni
        float   accruals_ratio
        float   working_capital
        float   asset_turnover
        string  updated_at
    }

    short_interest {
        string  ticker                 PK
        string  date                   PK
        float   shares_short
        float   short_ratio
        float   short_pct_float
        string  fetched_at
    }

    analyst_estimates {
        string  ticker                 PK
        string  date                   PK
        float   eps_estimate_fwd
        float   price_target
        integer num_analysts
        string  fetched_at
    }

    earnings_calendar {
        string  ticker                 PK
        string  earnings_date          PK
        float   eps_estimate
        string  fetched_at
    }

    earnings_transcripts {
        integer id                     PK
        string  ticker                 UK
        string  earnings_date          UK
        string  quarter
        integer year
        text    content
        string  fetched_at
        string  source_accession_no
    }

    sec_filings {
        integer id                     PK
        string  ticker
        string  form_type
        string  filed_date
        string  accession_no           UK
        string  filing_url
        text    content_text
        string  fetched_at
        boolean transcript_checked
    }

    insider_transactions {
        integer id                     PK
        string  ticker                 UK
        string  insider_name           UK
        string  insider_title
        string  transaction_type
        string  transaction_code
        float   shares                 UK
        float   price
        string  date                   UK
        string  ownership_type
        integer is_open_market
        integer is_ceo_cfo
        string  accession_no           UK
        string  fetched_at
    }

    insider_cluster_flags {
        string  ticker                 PK
        string  window_start           PK
        string  window_end
        integer insider_count
        float   total_shares
        string  flagged_at
    }

    institutional_holdings {
        integer id                     PK
        string  fund_name              UK
        string  ticker                 UK
        float   shares_held
        float   market_value
        string  report_date            UK
        string  filed_date
        string  fetched_at
    }

    institutional_summary {
        string  ticker                 PK
        string  report_date            PK
        integer funds_holding
        float   net_share_change
        integer new_positions
    }

    %% ---------------------------------------------------------------
    %% LAYER 2 — Factor scoring
    %% ---------------------------------------------------------------

    factor_scores {
        string  ticker                 PK
        string  score_date             PK
        string  sector
        string  regime
        float   mom_12_1
        float   mom_6m
        float   mom_3m
        float   mom_accel
        float   mom_52w_high
        float   mom_rel_strength
        float   momentum_score
        float   val_fwd_earn_yield
        float   val_book_to_price
        float   val_fcf_yield
        float   val_ev_ebitda_inv
        float   val_shareholder_yield
        float   val_sales_to_ev
        float   value_score
        float   qual_roe_stability
        float   qual_gm_level
        float   qual_gm_trend
        float   qual_de_inv
        float   qual_cfo_to_ni
        float   qual_accruals_inv
        float   qual_piotroski
        float   qual_altman_z
        float   quality_score
        float   grw_rev_yoy
        float   grw_earn_yoy
        float   grw_rev_accel
        float   grw_rd_intensity
        float   grw_fcf_yoy
        float   growth_score
        float   rev_30d
        float   rev_60d
        float   rev_90d
        float   revisions_score
        float   si_pct_float
        float   si_days_to_cover
        float   si_change
        float   short_interest_score
        float   ins_net_flow
        float   ins_cluster_flag
        float   insider_score
        float   inst_funds_holding
        float   inst_net_share_change
        float   inst_simultaneous_open
        float   institutional_score
        float   composite_score
        string  direction
        string  computed_at
    }

    regime_state {
        string  score_date             PK
        float   vix_close
        string  regime
        string  computed_at
    }

    crowding_flags {
        string  score_date             PK
        string  factor_a               PK
        string  factor_b               PK
        float   rolling_corr
        float   baseline_corr
        float   deviation
        integer flagged
        string  computed_at
    }

    %% ---------------------------------------------------------------
    %% LAYER 3 — AI analysis
    %% ---------------------------------------------------------------

    analysis_results {
        string  analyzer               PK
        string  ticker                 PK
        string  artifact_id            PK
        string  model
        text    result_json
        integer prompt_tokens
        integer completion_tokens
        float   cost_usd
        string  created_at
        string  expires_at
    }

    ai_scores {
        string  ticker                 PK
        string  score_date             PK
        float   earnings_score
        float   filing_score
        float   risk_score
        float   insider_ai_score
        float   ai_composite
        integer analyzers_used
        string  computed_at
    }

    combined_scores {
        string  ticker                 PK
        string  score_date             PK
        float   quant_composite
        float   ai_composite
        float   combined_score
        string  direction
        string  computed_at
    }

    %% ---------------------------------------------------------------
    %% LAYER 4 — Portfolio
    %% ---------------------------------------------------------------

    portfolio_positions {
        string  ticker                 PK
        string  direction
        float   shares
        float   entry_price
        string  entry_date
        float   current_price
        float   market_value
        float   weight
        float   unrealized_pnl
        string  sector
        float   combined_score
        float   beta
        string  updated_at
    }

    portfolio_history {
        integer id                     PK
        string  snapshot_date
        string  ticker
        string  direction
        float   shares
        float   price
        float   market_value
        float   weight
        float   unrealized_pnl
        string  sector
        float   combined_score
        string  recorded_at
    }

    position_approvals {
        integer id                     PK
        string  rebalance_date
        string  ticker
        string  action
        float   target_shares
        float   current_shares
        float   delta_shares
        float   estimated_cost_usd
        string  status
        string  created_at
        string  reviewed_at
    }

    %% ---------------------------------------------------------------
    %% LAYER 5 — Risk
    %% ---------------------------------------------------------------

    risk_log {
        integer id                     PK
        string  run_date
        string  check_type
        string  ticker
        string  result
        string  reason
        string  recorded_at
    }

    risk_events {
        integer id                     PK
        string  event_date
        string  event_type
        string  trigger
        string  detail
        string  recorded_at
    }

    %% ---------------------------------------------------------------
    %% LAYER 6 — Execution
    %% ---------------------------------------------------------------

    execution_orders {
        integer id                     PK
        string  rebalance_date
        string  ticker
        string  action
        float   ordered_shares
        float   filled_shares
        float   avg_fill_price
        string  order_id
        string  status
        float   slippage_bps
        string  created_at
        string  updated_at
    }

    %% ---------------------------------------------------------------
    %% LAYER 7 — Reporting
    %% ---------------------------------------------------------------

    pnl_attribution {
        string  date                   PK
        float   portfolio_return
        float   spy_return
        float   beta_pnl
        float   sector_pnl
        float   factor_pnl
        float   alpha_pnl
        float   net_beta
        string  computed_at
    }

    portfolio_nav {
        string  date                   PK
        float   nav
        float   spy_close
        float   drawdown_pct
        string  computed_at
    }

    position_trades {
        integer id                     PK
        string  ticker
        string  direction
        string  entry_date
        string  exit_date
        float   entry_price
        float   exit_price
        float   shares
        float   realized_pnl
        integer holding_days
        string  sector
        float   entry_score
        float   entry_vix
    }

    lp_letters {
        string  letter_date            PK
        string  doc_id
        text    content
        string  generated_at
    }

    weekly_commentary {
        string  week_start             PK
        text    content
        string  generated_at
    }

    %% ---------------------------------------------------------------
    %% RELATIONSHIPS
    %% ---------------------------------------------------------------

    sp500_universe       ||--o{ daily_prices             : "ticker"
    sp500_universe       ||--o{ fundamentals             : "ticker"
    sp500_universe       ||--o{ short_interest           : "ticker"
    sp500_universe       ||--o{ analyst_estimates        : "ticker"
    sp500_universe       ||--o{ earnings_calendar        : "ticker"
    sp500_universe       ||--o{ earnings_transcripts     : "ticker"
    sp500_universe       ||--o{ sec_filings              : "ticker"
    sp500_universe       ||--o{ insider_transactions     : "ticker"
    sp500_universe       ||--o{ insider_cluster_flags    : "ticker"
    sp500_universe       ||--o{ institutional_holdings   : "ticker"
    sp500_universe       ||--o{ institutional_summary    : "ticker"
    sp500_universe       ||--o{ factor_scores            : "ticker"

    benchmark_tickers    ||--o{ daily_prices             : "ticker"

    sec_filings          ||--o{ insider_transactions     : "accession_no"

    insider_transactions ||--o{ insider_cluster_flags    : "ticker (window aggregation)"
    institutional_holdings ||--|| institutional_summary  : "ticker + report_date (aggregated)"

    regime_state         ||--o{ factor_scores            : "score_date (regime label)"
    factor_scores        ||--o{ crowding_flags           : "score_date"

    factor_scores        ||--o| ai_scores                : "ticker + score_date"
    earnings_transcripts ||--o{ analysis_results         : "ticker (earnings analyzer input)"
    sec_filings          ||--o{ analysis_results         : "ticker (filing analyzer input)"
    insider_transactions ||--o{ analysis_results         : "ticker (insider analyzer input)"
    ai_scores            |o--|| combined_scores          : "ticker + score_date (left join)"
    factor_scores        ||--|| combined_scores          : "ticker + score_date"

    combined_scores      ||--o{ portfolio_positions      : "ticker + combined_score"
    portfolio_positions  ||--o{ portfolio_history        : "ticker (snapshots)"
    portfolio_positions  ||--o{ position_approvals       : "ticker (rebalance proposals)"

    position_approvals   ||--o{ execution_orders         : "ticker + rebalance_date"
    position_approvals   ||--o{ risk_log                 : "ticker (pre-trade checks)"
    portfolio_positions  ||--o{ risk_log                 : "ticker (portfolio-level checks)"
    portfolio_history    ||--o{ portfolio_nav            : "snapshot_date (daily NAV)"
    portfolio_nav        ||--|| pnl_attribution          : "date"
    execution_orders     ||--o{ position_trades          : "ticker (FIFO round trips)"
    pnl_attribution      ||--o{ weekly_commentary        : "week (commentary input)"
    pnl_attribution      ||--o{ lp_letters               : "period (letter input)"
```

## Notes

- **No formal FK constraints** are enforced in the schema. All relationships are logical through `ticker` (and `accession_no` for the SEC→insider link). The diagram reflects intended data flow, not database-enforced integrity.
- **`ticker` type**: stored as `String` throughout. S&P 500 tickers use `-` in place of `.` (e.g. `BRK-B`). Benchmark tickers include ETF symbols (e.g. `SPY`, `TLT`, `HYG`) and index codes (e.g. `^VIX`).
- **Dates**: stored as `String` in `YYYY-MM-DD` format throughout rather than native date types.
- **Layer 2 tables** (`factor_scores`, `regime_state`, `crowding_flags`) share the same SQLAlchemy metadata object as Layer 1 and are created by `initialise_schema()` when `factors.db` has been imported.
- **Layer 3 tables** (`analysis_results`, `ai_scores`, `combined_scores`) similarly share the metadata via `analysis.db`.
- **Layers 4-7 tables** register on the same metadata via `portfolio.db`, `risk.db`, `execution.db` and `reporting.db`; `initialise_schema()` also adds columns introduced after a table was first created (e.g. `institutional_holdings.filed_date`).
- **`fundamentals.period_end`** is the nearest calendar month-end (packet 23). A refresh keeps stored figures where Yahoo returns blanks (packet 26).
- **`institutional_holdings.report_date`** is the quarter the 13F covers; `filed_date` is when it was filed (packet 17). Only S&P 500 tickers are stored.
- **`analysis_results`** also holds the Ticker page's saved filing summaries (`analyzer = 'doc_summary'`, `artifact_id` = accession number).
- **This diagram's columns are generated from the SQLAlchemy table definitions**; regenerate rather than hand-edit when the schema changes.
