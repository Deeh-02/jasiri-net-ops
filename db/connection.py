import os
from datetime import datetime, timezone, timedelta
import psycopg2

DATABASE_URL = os.environ.get("DATABASE_URL")

# East Africa Time has no DST, so a fixed UTC+3 offset is exact and doesn't
# depend on the IANA tz database being installed on whatever OS this runs
# on (a minimal container image may not have one) — unlike
# zoneinfo.ZoneInfo("Africa/Nairobi"), this always works.
EAT = timezone(timedelta(hours=3))

def get_connection():
    if DATABASE_URL:
        # Production (Render) — connects to Supabase using the env variable
        conn = psycopg2.connect(DATABASE_URL, sslmode="require")
    else:
        # Local dev fallback — your existing local Postgres setup
        conn = psycopg2.connect(
            dbname="battery_tracker",
            user="postgres",
            password="battery123",
            host="localhost",
            port="5432"
        )
    # All `timestamp without time zone` columns are written/read as UTC wall-clock,
    # regardless of the underlying server's own OS/default timezone — otherwise a
    # naive datetime coming back from a local dev Postgres (which may default to
    # system local time) means something different than the same column in
    # production. Pinning the session here makes every `now()` and every value
    # read back an unambiguous UTC instant, which utc_iso() below then labels
    # explicitly when it goes out over JSON.
    cur = conn.cursor()
    cur.execute("SET TIME ZONE 'UTC';")
    cur.close()
    return conn

def utc_iso(dt):
    """Serializes a naive (UTC, per the session TIME ZONE set above) datetime
    to an explicit UTC ISO 8601 string. Plain `.isoformat()` on a naive
    datetime omits the offset entirely, which makes browsers parse it as
    local time instead of UTC — the "Z" suffix here is what lets the
    frontend convert it to EAT (or anything else) correctly."""
    return dt.isoformat() + "Z" if dt else None

def now_eat():
    """Current time as an EAT-aware datetime — for any business logic (like
    Check Sites' 8am-8pm active window) that needs to reason about the
    actual wall-clock hour in Nairobi, not whatever timezone the server
    process happens to be running in."""
    return datetime.now(timezone.utc).astimezone(EAT)

def to_eat(dt):
    """Converts one of this app's naive UTC datetimes (see get_connection's
    SET TIME ZONE above) to an EAT-aware one."""
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc).astimezone(EAT)
