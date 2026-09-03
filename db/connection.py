import os
import psycopg2

DATABASE_URL = os.environ.get("DATABASE_URL")

def get_connection():
    if DATABASE_URL:
        # Production (Render) — connects to Supabase using the env variable
        return psycopg2.connect(DATABASE_URL, sslmode="require")
    # Local dev fallback — your existing local Postgres setup
    return psycopg2.connect(
        dbname="battery_tracker",
        user="postgres",
        password="battery123",
        host="localhost",
        port="5432"
    )