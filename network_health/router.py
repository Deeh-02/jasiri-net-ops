"""Network Health's HTTP endpoints, all under /network/health/.

INGEST follows docs/network-health-ingest-contract.md: the Frankfurt
shipper posts batches with "Authorization: Bearer <NETWORK_HEALTH_TOKEN>".
That token can only post readings. The answer is 2xx only once the batch is
stored (anything else makes the shipper resend it); unknown kinds, unknown
fields and records that can't be read are skipped, not refused, so one odd
line can never block the feed.

STATUS is for an outside uptime checker: ?key=<NH_STATUS_KEY>, 200 when
nothing is RED or OFFLINE, 503 otherwise. Calling it runs the silence checks.

READS need Network Health > View on the role; Settings and the manual
ONT <-> PPPoE matches need Network Health > Admin (admins always pass both).

DIAGNOSE is a chat with Claude about the network (diagnose.py). Sonnet needs
View; Opus (costs more) needs Admin.
"""
import hmac
import json
import logging
import os
import threading
import time
from collections import deque
from datetime import datetime
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from routers.auth import get_current_user
from routers.permissions import user_has_permission

from network_health import alerts, config, diagnose, service, store

router = APIRouter()
log = logging.getLogger(__name__)

_recent_posts = deque()
_rate_lock = threading.Lock()


class Batch(BaseModel):
    """The shipper's envelope. Records are read one by one in feed.py."""
    model_config = ConfigDict(extra="ignore")
    source: Optional[str] = Field(None, max_length=64)
    sent_at: Optional[datetime] = None
    records: list[Any] = Field(default_factory=list, max_length=2000)


def _reject(status_code, reason):
    """Logs why a post was refused (visible in the Render log) and refuses it."""
    log.warning("network health ingest rejected (%s): %s", status_code, reason)
    raise HTTPException(status_code=status_code, detail=reason)


def token_is_valid(authorization):
    """Constant-time comparison with NETWORK_HEALTH_TOKEN. An unset token
    refuses everything: an empty secret must never let a post in."""
    expected = (os.environ.get("NETWORK_HEALTH_TOKEN") or "").strip()
    sent = (authorization or "").removeprefix("Bearer ").strip()
    if not expected or not sent:
        return False
    return hmac.compare_digest(sent.encode(), expected.encode())


def _check_rate():
    now = time.monotonic()
    with _rate_lock:
        while _recent_posts and now - _recent_posts[0] > 60:
            _recent_posts.popleft()
        if len(_recent_posts) >= config.INGEST_LIMIT_PER_MINUTE:
            _reject(429, "too many posts this minute; resend later")
        _recent_posts.append(now)


@router.post("/network/health/ingest")
async def ingest(request: Request):
    if not token_is_valid(request.headers.get("authorization")):
        # Lengths only, never values: tells "token not set" (0) from "different".
        configured = len((os.environ.get("NETWORK_HEALTH_TOKEN") or "").strip())
        log.warning("network health ingest: wrong token (configured length %s)", configured)
        raise HTTPException(status_code=401, detail="invalid token")
    _check_rate()
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > config.MAX_BODY_BYTES:
        _reject(413, "batch too large")
    body = await request.body()
    if len(body) > config.MAX_BODY_BYTES:
        _reject(413, "batch too large")
    try:
        batch = Batch.model_validate(json.loads(body))
    except ValueError as e:        # bad JSON, or ValidationError (a ValueError) for a bad envelope
        detail = e.errors()[0]["msg"] if isinstance(e, ValidationError) else "body is not valid JSON"
        _reject(422, f"not a batch: {detail}")
    try:
        result = await run_in_threadpool(service.ingest, batch.source, batch.sent_at, batch.records)
    except Exception:
        log.exception("network health ingest: storing the batch failed")
        raise HTTPException(status_code=503, detail="couldn't store the batch; resend it")
    return {"ok": True, **result}


@router.get("/network/health/status")
def health_status(key: str = Query("")):
    expected = (os.environ.get("NH_STATUS_KEY") or "").strip()
    if not expected or not hmac.compare_digest(key.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="invalid key")
    code, body = service.status()
    return JSONResponse(body, status_code=code)


# ---- Reads ----

def require_view(current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "network_health", "view"):
        raise HTTPException(status_code=403, detail="You don't have permission to view Network Health")
    return current_user


def require_health_admin(current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "network_health", "admin"):
        raise HTTPException(status_code=403, detail="You don't have permission to change Network Health settings")
    return current_user


@router.get("/network/health/overview")
def get_overview(current_user: dict = Depends(require_view)):
    return service.overview()


@router.get("/network/health/charts")
def get_charts(range: Literal["24h", "7d"] = "24h", current_user: dict = Depends(require_view)):
    return service.charts(range)


@router.get("/network/health/routes")
def get_routes(current_user: dict = Depends(require_view)):
    return service.routes_card()


@router.get("/network/health/inside")
def get_inside(range: Literal["24h", "7d"] = "24h", current_user: dict = Depends(require_view)):
    return service.inside_card(range)


@router.get("/network/health/areas")
def get_areas(current_user: dict = Depends(require_view)):
    return service.areas_card()


@router.get("/network/health/sms")
def get_sms(days: int = Query(1, ge=1, le=30), current_user: dict = Depends(require_view)):
    return service.sms_card(days)


@router.get("/network/health/box")
def get_box(dev: Optional[str] = Query(None, max_length=40), current_user: dict = Depends(require_view)):
    return service.box_card(dev)


class BoxMarker(BaseModel):
    """A manual marker on the Customer line charts (e.g. "CRS-OLT DAC swapped")."""
    model_config = ConfigDict(extra="forbid")
    at: datetime
    label: str = Field(min_length=1, max_length=config.BOX_MARKER_LABEL_MAX)


@router.post("/network/health/box/markers")
def post_box_marker(body: BoxMarker, current_user: dict = Depends(require_health_admin)):
    label = " ".join(body.label.split())
    if not label:
        raise HTTPException(status_code=422, detail="Give the marker a label")
    if body.at.tzinfo is None:
        raise HTTPException(status_code=422, detail="The marker's time needs a time zone (e.g. +03:00)")
    marker_id = store.save_box_marker(body.at, label, current_user.get("id") or None)
    if marker_id is None:
        raise HTTPException(status_code=409, detail="Too many markers: delete an old one first")
    return {"ok": True, "id": marker_id}


@router.delete("/network/health/box/markers")
def delete_box_marker(id: str = Query(..., min_length=1, max_length=40, pattern=r"^[0-9a-f]+$"),
                      current_user: dict = Depends(require_health_admin)):
    if not store.delete_box_marker(id):
        raise HTTPException(status_code=404, detail="No such marker")
    return {"ok": True}


@router.get("/network/health/access/thresholds")
def get_access_thresholds(current_user: dict = Depends(require_view)):
    return service.access_thresholds()


@router.get("/network/health/access")
def get_access(current_user: dict = Depends(require_view)):
    return service.access_card()


class OntMatch(BaseModel):
    """A manual ONT <-> PPPoE match. no_session: this ONT has no PPPoE
    session of its own (it is never cross-checked)."""
    model_config = ConfigDict(extra="forbid")
    pon: str = Field(min_length=1, max_length=20)
    ont_id: int = Field(ge=0, le=100000)
    ppp_name: Optional[str] = Field(None, max_length=64)
    no_session: bool = False


@router.put("/network/health/access/matches")
def put_access_match(body: OntMatch, current_user: dict = Depends(require_health_admin)):
    name = (body.ppp_name or "").strip() or None
    if not body.no_session and name is None:
        raise HTTPException(status_code=422, detail="Give the PPPoE session name, or mark it as having none")
    store.save_ont_match(body.pon.strip(), body.ont_id, None if body.no_session else name, current_user.get("id") or None)
    return {"ok": True}


@router.delete("/network/health/access/matches")
def delete_access_match(pon: str = Query(..., min_length=1, max_length=20), ont_id: int = Query(..., ge=0),
                        current_user: dict = Depends(require_health_admin)):
    if not store.delete_ont_match(pon, ont_id):
        raise HTTPException(status_code=404, detail="No manual match for that ONT")
    return {"ok": True}


@router.get("/network/health/incidents")
def get_incidents(limit: int = Query(100, ge=1, le=500), current_user: dict = Depends(require_view)):
    return store.incidents(limit)


# ---- Diagnose: a chat with Claude ----

class ChatTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=config.DIAGNOSE_ANSWER_MAX)


class DiagnoseChat(BaseModel):
    conversation_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9-]+$")
    model: Literal["sonnet", "opus"] = "sonnet"
    effort: Literal["medium", "high"] = "medium"
    messages: list[ChatTurn] = Field(min_length=1, max_length=config.DIAGNOSE_MAX_TURNS)


def can_use_opus(current_user):
    return user_has_permission(current_user, "network_health", "admin")


@router.get("/network/health/diagnose")
def get_diagnose(current_user: dict = Depends(require_view)):
    """Whether Claude can be asked, the models and efforts on offer, today's counts."""
    return {**diagnose.info(), "can_opus": can_use_opus(current_user)}


@router.post("/network/health/diagnose/chat")
def post_diagnose_chat(body: DiagnoseChat, current_user: dict = Depends(require_view)):
    if body.model == "opus" and not can_use_opus(current_user):
        raise HTTPException(status_code=403, detail="Claude Opus needs Network Health > Admin")
    try:
        return diagnose.chat(body.conversation_id, body.model, body.effort,
                             [t.model_dump() for t in body.messages])
    except diagnose.DiagnoseError as e:
        raise HTTPException(status_code=e.status, detail=e.message)


class SettingsUpdate(BaseModel):
    uplink_capacity_mbps: float = Field(gt=0, le=100000)
    provider_switched_at: Optional[datetime] = None


@router.get("/network/health/settings")
def get_settings(current_user: dict = Depends(require_health_admin)):
    with store.transaction() as cur:
        settings = store.settings(cur)
    return {"settings": settings, "thresholds": service.thresholds(),
            "sms": {"mode": alerts.sms_mode(), "cap": alerts.daily_cap(), "recipients": len(alerts.alert_phones())},
            "token_is_set": bool((os.environ.get("NETWORK_HEALTH_TOKEN") or "").strip()),
            "status_key_is_set": bool((os.environ.get("NH_STATUS_KEY") or "").strip())}


@router.put("/network/health/settings")
def put_settings(body: SettingsUpdate, current_user: dict = Depends(require_health_admin)):
    switched = body.provider_switched_at
    if switched is not None and switched.tzinfo is None:
        raise HTTPException(status_code=422, detail="provider_switched_at needs a time zone (e.g. +03:00)")
    store.save_settings(body.uplink_capacity_mbps, switched, current_user.get("id") or None)
    return {"ok": True}
