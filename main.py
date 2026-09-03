from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from routers import auth, permissions, sites, batteries, users

app = FastAPI()

app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(auth.router)
app.include_router(permissions.router)
app.include_router(sites.router)
app.include_router(batteries.router)
app.include_router(users.router)

@app.get("/")
def serve_dashboard():
    return FileResponse("static/index.html")
