import os
import psycopg
from psycopg.rows import dict_row

DSN = os.environ.get("DATABASE_URL", "postgresql://app:app@localhost:54402/pvivscan")


def connect():
    return psycopg.connect(DSN, row_factory=dict_row)


# 台账只有一份事实来源：transformers.oil_expiry_date。
# 拦截口（报送/工人）与顶栏台账专页读的都是同一张表的这一列。
SCHEMA = """
CREATE TABLE IF NOT EXISTS iv_scans (
    id serial PRIMARY KEY,
    string_code text NOT NULL,
    voc_v double precision NOT NULL,
    isc_a double precision NOT NULL,
    fill_factor double precision NOT NULL,
    status text NOT NULL DEFAULT 'pending',
    verdict text,
    reason text,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL,
    processed_at timestamptz
);
CREATE TABLE IF NOT EXISTS transformers (
    id serial PRIMARY KEY,
    name text NOT NULL UNIQUE,
    oil_expiry_date date NOT NULL,
    renewed_at timestamptz,
    renewed_by text,
    last_block_reason text,
    last_block_at timestamptz
);
CREATE TABLE IF NOT EXISTS oil_events (
    id serial PRIMARY KEY,
    transformer_id integer NOT NULL REFERENCES transformers(id),
    event_type text NOT NULL,
    detail text NOT NULL,
    scan_id integer REFERENCES iv_scans(id),
    created_by text NOT NULL,
    created_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_oil_events_tr ON oil_events(transformer_id, id DESC);
ALTER TABLE iv_scans
    ADD COLUMN IF NOT EXISTS transformer_id integer REFERENCES transformers(id);
CREATE OR REPLACE FUNCTION notify_iv_scan() RETURNS trigger AS $$
BEGIN
  PERFORM pg_notify('iv_scan_new', NEW.id::text);
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_iv_scan_notify ON iv_scans;
CREATE TRIGGER trg_iv_scan_notify
AFTER INSERT ON iv_scans
FOR EACH ROW EXECUTE FUNCTION notify_iv_scan();
"""
