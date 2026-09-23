from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from routers import auth, permissions, sites, batteries, users, inventory, monitoring, notifications

app = FastAPI()


@app.middleware("http")
async def no_cache_static(request, call_next):
    # Browsers were holding onto old dashboard.js/dashboard.css across
    # plain reloads during active development — no-cache forces a
    # revalidation request every time (still fast, StaticFiles' own
    # ETag/Last-Modified support returns a 304 when nothing changed) so an
    # edit shows up on the next normal refresh instead of needing Ctrl+Shift+R.
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(auth.router)
app.include_router(permissions.router)
app.include_router(sites.router)
app.include_router(batteries.router)
app.include_router(users.router)
app.include_router(inventory.router)
app.include_router(monitoring.router)
app.include_router(notifications.router)

@app.get("/")
def serve_dashboard():
    return FileResponse("static/index.html")

# PHASES.md 4.8 — "External uptime monitor on an Ops health endpoint,
# alerting distinctly from a site-down alert." Deliberately unauthenticated
# (an external pinger like UptimeRobot/Better Uptime can't hold a JWT) and
# deliberately does nothing but prove the process is alive — no DB round
# trip, so it can't false-negative on a slow Supabase connection.
@app.get("/health")
def health():
    return {"ok": True}
