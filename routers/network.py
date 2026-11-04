"""Network page (CCR2116): the router's ingest endpoint and the page's reads.

INGEST is token-auth, not JWT, for the same reason as /monitoring/ingest (a
RouterOS script can't hold a JWT — see ARCHITECTURE.md "Monitoring ingest
auth"). One token per router, from NETWORK_INGEST_TOKEN_<SLUG> (e.g.
NETWORK_INGEST_TOKEN_CCR2116), sent as "Authorization: Bearer <token>". It
can only post readings. Unlike /monitoring/ingest, bad data is answered with
a 4xx (this feature's scope asks for that); every rejection is logged with
its reason so a broken script is still visible from the Render log.

READS need Sites > Network on the role (sites:view_network; admins always).
Nothing here returns phone numbers, payment amounts or PPP passwords — the
router script never reads /ppp secret at all.
"""
import hmac
import json
import logging
import os
import re
import threading
import time
from collections import defaultdict, deque
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from db import network as db
from routers.auth import get_current_user
from routers.permissions import user_has_permission

router = APIRouter()
log = logging.getLogger(__name__)

MAX_BODY_BYTES = 128 * 1024          # RouterOS can't send more than 64 KB anyway
INGEST_LIMIT_PER_MINUTE = 60         # live every 15 s + a few session parts a minute
SLUG = re.compile(r"^[a-z0-9-]{1,32}$")


# ---- Message shapes ----

def _number_or_none(value):
    """SFP and health readings arrive as numbers on some RouterOS builds and
    as text like "-6.123dBm" or "45C" on others; keep the number."""
    if value is None or isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        match = re.match(r"\s*(-?\d+(?:\.\d+)?)", value)
        return float(match.group(1)) if match else None
    raise ValueError("expected a number")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Sfp(Strict):
    temp_c: Optional[float] = None
    rx_dbm: Optional[float] = None
    tx_dbm: Optional[float] = None

    _numbers = field_validator("temp_c", "rx_dbm", "tx_dbm", mode="before")(_number_or_none)


class Uplink(Strict):
    interface: str = Field(max_length=64)
    running: bool
    link_rate: Optional[str] = Field(default=None, max_length=32)
    rx_bps: int = Field(ge=0, le=100_000_000_000)
    tx_bps: int = Field(ge=0, le=100_000_000_000)
    rx_errors: Optional[int] = Field(default=None, ge=0)
    tx_errors: Optional[int] = Field(default=None, ge=0)
    rx_drops: Optional[int] = Field(default=None, ge=0)
    tx_drops: Optional[int] = Field(default=None, ge=0)
    sfp: Optional[Sfp] = None


class Gateway(Strict):
    address: Optional[str] = Field(default=None, max_length=64)
    source: Literal["auto", "config", "none"]
    alive: Optional[bool] = None
    loss_pct: Optional[float] = Field(default=None, ge=0, le=100)
    rtt_ms_avg: Optional[float] = Field(default=None, ge=0)
    rtt_ms_max: Optional[float] = Field(default=None, ge=0)
    arp: Optional[str] = Field(default=None, max_length=32)


class Internet(Strict):
    target: str = Field(max_length=64)
    alive: Optional[bool] = None
    loss_pct: Optional[float] = Field(default=None, ge=0, le=100)
    rtt_ms_avg: Optional[float] = Field(default=None, ge=0)


class System(Strict):
    cpu_pct: Optional[float] = Field(default=None, ge=0, le=100)
    mem_used_pct: Optional[float] = Field(default=None, ge=0, le=100)
    temp_c: Optional[float] = None
    routeros: Optional[str] = Field(default=None, max_length=40)

    _numbers = field_validator("temp_c", mode="before")(_number_or_none)


class RouterEvent(Strict):
    event_id: str = Field(min_length=1, max_length=64)
    type: Literal["gateway_down"]
    duration_s: int = Field(ge=0, le=90 * 86400)
    ended_ago_s: int = Field(ge=0, le=90 * 86400)


class LiveMessage(Strict):
    router: str
    seq: Optional[int] = Field(default=None, ge=0)
    uptime_s: int = Field(ge=0)
    uplink: Uplink
    gateway: Gateway
    internet: Optional[Internet] = None
    system: System
    pppoe_online: int = Field(ge=0, le=100_000)
    events: list[RouterEvent] = Field(default_factory=list, max_length=20)


class Session(Strict):
    name: str = Field(min_length=1, max_length=128)
    address: Optional[str] = Field(default=None, max_length=45)
    caller_id: Optional[str] = Field(default=None, max_length=64)
    uptime_s: Optional[int] = Field(default=None, ge=0)
    service: Optional[str] = Field(default=None, max_length=32)
    session_id: Optional[str] = Field(default=None, max_length=64)


class SessionsMessage(Strict):
    router: str
    batch_id: str = Field(min_length=1, max_length=40)
    part: int = Field(ge=1, le=200)
    parts: int = Field(ge=1, le=200)
    rows: list[Session] = Field(max_length=200)


# ---- Ingest ----

_recent_posts = defaultdict(deque)
_rate_lock = threading.Lock()


def _check_rate(slug):
    now = time.monotonic()
    with _rate_lock:
        times = _recent_posts[slug]
        while times and now - times[0] >= 60:
            times.popleft()
        if len(times) >= INGEST_LIMIT_PER_MINUTE:
            raise HTTPException(status_code=429, detail="Too many readings from this router")
        times.append(now)


def _reject(status, reason, slug=None):
    log.warning("network ingest rejected (%s): %s router=%s", status, reason, slug)
    raise HTTPException(status_code=status, detail=reason)


def check_token(authorization, slug):
    """True if `authorization` is 'Bearer <this router's token>'. Constant-time.
    A router with no token configured accepts nothing (fails closed)."""
    expected = os.environ.get(f"NETWORK_INGEST_TOKEN_{slug.upper().replace('-', '_')}")
    if not expected or not authorization or not authorization.startswith("Bearer "):
        return False
    return hmac.compare_digest(authorization[len("Bearer "):].encode(), expected.encode())


def parse_message(data):
    """LiveMessage or SessionsMessage, or raises ValueError/ValidationError."""
    if not isinstance(data, dict):
        raise ValueError("body must be a JSON object")
    if "uplink" in data:
        return LiveMessage.model_validate(data)
    if "batch_id" in data:
        return SessionsMessage.model_validate(data)
    raise ValueError("neither a live message (uplink) nor a session list (batch_id)")


@router.post("/network/ingest")
async def ingest(request: Request):
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        _reject(413, "payload too large")
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        _reject(413, "payload too large")
    try:
        data = json.loads(body)
    except ValueError:
        _reject(400, "body is not valid JSON")
    slug = data.get("router") if isinstance(data, dict) else None
    if not isinstance(slug, str) or not SLUG.match(slug):
        _reject(401, "missing or invalid router")
    # Token before anything else about the router, so an unauthenticated
    # caller learns nothing about which routers exist.
    if not check_token(request.headers.get("authorization"), slug):
        _reject(401, "invalid ingest token", slug)
    known = db.get_router(slug)
    if known is None or not known["is_enabled"]:
        _reject(403, "unknown or disabled router", slug)
    _check_rate(slug)
    try:
        message = parse_message(data)
    except ValidationError as e:
        _reject(422, f"invalid message: {e.errors()[0]['loc']} {e.errors()[0]['msg']}", slug)
    except ValueError as e:
        _reject(422, str(e), slug)
    if isinstance(message, LiveMessage):
        return {"ok": True, **db.record_live(slug, message.model_dump())}
    if message.part > message.parts:
        _reject(422, "part is larger than parts", slug)
    return {"ok": True, **db.record_session_part(slug, message.model_dump())}


# ---- Reads ----

def require_network_view(current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "sites", "view_network"):
        raise HTTPException(status_code=403, detail="You don't have permission to view the network page")
    return current_user


def _found(result):
    if result is None:
        raise HTTPException(status_code=404, detail="Unknown router")
    return result


@router.get("/network/overview")
def network_overview(router: str = "ccr2116", current_user: dict = Depends(require_network_view)):
    return _found(db.overview(router))


@router.get("/network/pppoe")
def network_pppoe(router: str = "ccr2116", current_user: dict = Depends(require_network_view)):
    return _found(db.pppoe(router))


@router.get("/network/uplink-history")
def network_uplink_history(router: str = "ccr2116", range: str = "1h",
                           current_user: dict = Depends(require_network_view)):
    if db.get_router(router) is None:
        raise HTTPException(status_code=404, detail="Unknown router")
    try:
        return db.uplink_history(router, range)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/network/events")
def network_events(router: str = "ccr2116", limit: int = 50, current_user: dict = Depends(require_network_view)):
    if db.get_router(router) is None:
        raise HTTPException(status_code=404, detail="Unknown router")
    return {"events": db.events(router, limit=max(1, min(limit, 100)))}
