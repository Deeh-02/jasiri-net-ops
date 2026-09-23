-- Phase 4.8 follow-up — down-alert escalation schedule. One new column,
-- nothing existing is altered or dropped.
--
-- WHY A COLUMN, NOT A NEW TABLE. site_alert_episodes (0012) already tracks
-- one open row per down/flapping episode, and down_alert_sent_at already
-- records "when was the down alert last sent" for that episode. Escalation
-- only needs ONE more fact on top of that: how far through the reminder
-- schedule (db/monitoring_alerts.py's SCHEDULE_MINUTES) this episode has
-- gotten. escalation_step=0 means "not yet alerted at all" (unchanged from
-- before this migration); each reminder sent increments it by one; once it
-- reaches len(SCHEDULE_MINUTES) the episode has exhausted the schedule
-- (12h in) and goes quiet on its own — no separate "stop" flag needed.
--
-- ACKNOWLEDGEMENT NOTE (checked, not changed here): site_acknowledgements
-- (0011) is already keyed by monitored_site_id, not by recipient — one ack
-- suppresses sends to every recipient on that site, and since
-- uq_site_alert_episode_open allows only one open episode per site, an ack
-- is already effectively per-episode. Nothing in this file changes that.
--
-- Run this once against the target database:
--   psql "$DATABASE_URL" -f migrations/0013_alert_escalation_step.sql

ALTER TABLE site_alert_episodes
    ADD COLUMN IF NOT EXISTS escalation_step integer NOT NULL DEFAULT 0;
