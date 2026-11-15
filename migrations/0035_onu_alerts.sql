-- Phase 6: LOS alerts for ONUs (customers and hotspot sites). Approved by
-- the owner on 2026-11-11.
--
-- One row per LOS episode: a single ONU, or a whole PON port when 3 or more
-- ONUs on it lost signal within 2 minutes (likely one fibre cut). Written
-- only by db/onu_alerts.py. step counts the messages sent from the fast
-- schedule (2 min, 15 min, 30 min, 1 h, 3 h, 6 h, 8 h); after that, or once
-- acknowledged, reminders go twice a day (08:00 and 16:00 EAT) inside the
-- alert sending window, until the ONU is back.
--
-- Run this once against the target database:
--   psql "$DATABASE_URL" -f migrations/0035_onu_alerts.sql

BEGIN;

CREATE TABLE IF NOT EXISTS onu_alerts (
    id                  bigserial PRIMARY KEY,
    unique_external_id  text NOT NULL,        -- the ONU (for a PON alert, the first one seen)
    pon                 text,                 -- "0/3/4" for a whole-port alert, else NULL
    onu_count           int NOT NULL DEFAULT 1,
    started_at          timestamptz NOT NULL, -- when SmartOLT says it went LOS
    step                int NOT NULL DEFAULT 0,
    last_sent_at        timestamptz,
    acked_by            int REFERENCES users(id) ON DELETE SET NULL,
    acked_at            timestamptz,
    ack_note            text,
    ended_at            timestamptz
);

-- One open alert per ONU, and one open alert per PON port.
CREATE UNIQUE INDEX IF NOT EXISTS onu_alerts_open_onu ON onu_alerts (unique_external_id) WHERE ended_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS onu_alerts_open_pon ON onu_alerts (pon) WHERE ended_at IS NULL AND pon IS NOT NULL;

COMMIT;

-- ROLLBACK:
--   DROP TABLE onu_alerts;
