"""Layer 5 table definitions — registered on the shared data.db metadata."""

import sqlalchemy as sa

from data.db import metadata

risk_log = sa.Table(
    "risk_log", metadata,
    sa.Column("id",          sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("run_date",    sa.String,  nullable=False),
    sa.Column("check_type",  sa.String,  nullable=False),   # e.g. 'pre_trade', 'circuit_breaker', 'tail_risk'
    sa.Column("ticker",      sa.String),                    # nullable
    sa.Column("result",      sa.String,  nullable=False),   # 'APPROVED','REJECTED','WARNING','TRIGGERED'
    sa.Column("reason",      sa.String),
    sa.Column("recorded_at", sa.String),                    # ISO timestamp
)

sa.Index("idx_risk_log_date_check", risk_log.c.run_date, risk_log.c.check_type)

risk_events = sa.Table(
    "risk_events", metadata,
    sa.Column("id",          sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("event_date",  sa.String,  nullable=False),
    sa.Column("event_type",  sa.String,  nullable=False),   # e.g. 'SIZE_DOWN_30','CLOSE_ALL','KILL_SWITCH','REDUCE_GROSS_20','REDUCE_GROSS_50','FORCE_CLOSE'
    sa.Column("trigger",     sa.String),
    sa.Column("detail",      sa.String),                    # JSON blob
    sa.Column("recorded_at", sa.String),
)

sa.Index("idx_risk_events_date", risk_events.c.event_date)
