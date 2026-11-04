from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from routers import auth, permissions, sites, batteries, users, inventory, monitoring, notifications, customers, chat, network

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
app.include_router(customers.router)
app.include_router(chat.router)
app.include_router(network.router)

@app.get("/")
def serve_dashboard():
    return FileResponse("static/index.html")


# The service worker must be served from the site root to control every page
# (a worker under /static/ could only control /static/*), and the manifest
# gets its own media type. no-cache so a new sw.js is picked up promptly.
@app.get("/sw.js")
def serve_service_worker():
    return FileResponse("static/sw.js", media_type="application/javascript", headers={"Cache-Control": "no-cache"})


@app.get("/manifest.webmanifest")
def serve_manifest():
    return FileResponse("static/manifest.webmanifest", media_type="application/manifest+json", headers={"Cache-Control": "no-cache"})

# PHASES.md 4.8 — "External uptime monitor on an Ops health endpoint,
# alerting distinctly from a site-down alert." Deliberately unauthenticated
# (an external pinger like UptimeRobot/Better Uptime can't hold a JWT) and
# deliberately does nothing but prove the process is alive — no DB round
# trip, so it can't false-negative on a slow Supabase connection.
@app.get("/health")
def health():
    return {"ok": True}
