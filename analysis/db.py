"""Layer 3 table definitions — registered on the shared data.db metadata."""

import sqlalchemy as sa

from data.db import metadata

analysis_results = sa.Table(
    "analysis_results", metadata,
    sa.Column("analyzer",          sa.String,  nullable=False),
    sa.Column("ticker",            sa.String,  nullable=False),
    sa.Column("artifact_id",       sa.String,  nullable=False),
    sa.Column("model",             sa.String),
    sa.Column("result_json",       sa.Text),
    sa.Column("prompt_tokens",     sa.Integer),
    sa.Column("completion_tokens", sa.Integer),
    sa.Column("cost_usd",          sa.Float),
    sa.Column("created_at",        sa.String),
    sa.Column("expires_at",        sa.String),
    sa.PrimaryKeyConstraint("analyzer", "ticker", "artifact_id"),
)


ai_scores = sa.Table(
    "ai_scores", metadata,
    sa.Column("ticker",          sa.String, nullable=False),
    sa.Column("score_date",      sa.String, nullable=False),
    sa.Column("earnings_score",  sa.Float),
    sa.Column("filing_score",    sa.Float),
    sa.Column("risk_score",      sa.Float),
    sa.Column("insider_ai_score", sa.Float),
    sa.Column("ai_composite",    sa.Float),
    sa.Column("analyzers_used",  sa.Integer),
    sa.Column("computed_at",     sa.String),
    sa.PrimaryKeyConstraint("ticker", "score_date"),
)

combined_scores = sa.Table(
    "combined_scores", metadata,
    sa.Column("ticker",          sa.String, nullable=False),
    sa.Column("score_date",      sa.String, nullable=False),
    sa.Column("quant_composite", sa.Float),
    sa.Column("ai_composite",    sa.Float),
    sa.Column("combined_score",  sa.Float),
    sa.Column("direction",       sa.String),
    sa.Column("computed_at",     sa.String),
    sa.PrimaryKeyConstraint("ticker", "score_date"),
)
