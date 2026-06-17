"""Layer 2 table definitions — registered on the shared data.db metadata.

Importing this module is enough to make initialise_schema() create these
tables. run_scoring.py imports it before calling initialise_schema().
"""

import sqlalchemy as sa

# Register on the same metadata object so data.db.initialise_schema() picks
# up Layer 2 tables automatically.
from data.db import metadata

factor_scores = sa.Table(
    "factor_scores", metadata,
    sa.Column("ticker",                  sa.String,  nullable=False),
    sa.Column("score_date",              sa.String,  nullable=False),
    sa.Column("sector",                  sa.String),
    sa.Column("regime",                  sa.String),
    # Momentum sub-factors (sector percentile 0-100)
    sa.Column("mom_12_1",               sa.Float),
    sa.Column("mom_6m",                 sa.Float),
    sa.Column("mom_3m",                 sa.Float),
    sa.Column("mom_accel",              sa.Float),
    sa.Column("mom_52w_high",           sa.Float),
    sa.Column("mom_rel_strength",       sa.Float),
    sa.Column("momentum_score",         sa.Float),
    # Value sub-factors
    sa.Column("val_fwd_earn_yield",     sa.Float),
    sa.Column("val_book_to_price",      sa.Float),
    sa.Column("val_fcf_yield",          sa.Float),
    sa.Column("val_ev_ebitda_inv",      sa.Float),
    sa.Column("val_shareholder_yield",  sa.Float),
    sa.Column("val_sales_to_ev",        sa.Float),
    sa.Column("value_score",            sa.Float),
    # Quality sub-factors
    sa.Column("qual_roe_stability",     sa.Float),
    sa.Column("qual_gm_level",          sa.Float),
    sa.Column("qual_gm_trend",          sa.Float),
    sa.Column("qual_de_inv",            sa.Float),
    sa.Column("qual_cfo_to_ni",         sa.Float),
    sa.Column("qual_accruals_inv",      sa.Float),
    sa.Column("qual_piotroski",         sa.Float),
    sa.Column("qual_altman_z",          sa.Float),
    sa.Column("quality_score",          sa.Float),
    # Growth sub-factors
    sa.Column("grw_rev_yoy",            sa.Float),
    sa.Column("grw_earn_yoy",           sa.Float),
    sa.Column("grw_rev_accel",          sa.Float),
    sa.Column("grw_rd_intensity",       sa.Float),
    sa.Column("grw_fcf_yoy",            sa.Float),
    sa.Column("growth_score",           sa.Float),
    # Estimate revisions sub-factors
    sa.Column("rev_30d",                sa.Float),
    sa.Column("rev_60d",                sa.Float),
    sa.Column("rev_90d",                sa.Float),
    sa.Column("revisions_score",        sa.Float),
    # Short interest sub-factors
    sa.Column("si_pct_float",           sa.Float),
    sa.Column("si_days_to_cover",       sa.Float),
    sa.Column("si_change",              sa.Float),
    sa.Column("short_interest_score",   sa.Float),
    # Insider sub-factors
    sa.Column("ins_net_flow",           sa.Float),
    sa.Column("ins_cluster_flag",       sa.Float),
    sa.Column("insider_score",          sa.Float),
    # Institutional sub-factors
    sa.Column("inst_funds_holding",     sa.Float),
    sa.Column("inst_net_share_change",  sa.Float),
    sa.Column("inst_simultaneous_open", sa.Float),
    sa.Column("institutional_score",    sa.Float),
    # Composite
    sa.Column("composite_score",        sa.Float),
    sa.Column("direction",              sa.String),
    sa.Column("computed_at",            sa.String),
    sa.PrimaryKeyConstraint("ticker", "score_date"),
)

sa.Index("idx_factor_scores_date", factor_scores.c.score_date)

regime_state = sa.Table(
    "regime_state", metadata,
    sa.Column("score_date",   sa.String, primary_key=True),
    sa.Column("vix_close",    sa.Float),
    sa.Column("regime",       sa.String),
    sa.Column("computed_at",  sa.String),
)

crowding_flags = sa.Table(
    "crowding_flags", metadata,
    sa.Column("score_date",    sa.String, nullable=False),
    sa.Column("factor_a",      sa.String, nullable=False),
    sa.Column("factor_b",      sa.String, nullable=False),
    sa.Column("rolling_corr",  sa.Float),
    sa.Column("baseline_corr", sa.Float),
    sa.Column("deviation",     sa.Float),
    sa.Column("flagged",       sa.Integer),
    sa.Column("computed_at",   sa.String),
    sa.PrimaryKeyConstraint("score_date", "factor_a", "factor_b"),
)
