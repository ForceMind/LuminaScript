Alembic owns database schema changes from revision `20260728_0001` onward.

Existing SQLite installations are first normalized by the legacy compatibility
upgrade, stamped at the baseline revision, and then upgraded normally.
