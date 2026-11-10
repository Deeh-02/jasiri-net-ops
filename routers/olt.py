"""OLT page (Phase 6, SmartOLT): the Configured list, one ONU's details,
graphs, the Unconfigured list and Authorize.

READS need Sites > Network on the role (sites:view_network; admins always),
the same switch as Network Analysis. AUTHORIZE is admin only, and refused when
SMARTOLT_WRITES_ENABLED is false. Every reply has been through
smartolt.scrub(): no password ever reaches the browser.
"""
import logging
import re
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from psycopg2 import errors as pg_errors
from pydantic import BaseModel, ConfigDict, Field, field_validator

from db import smartolt
from routers.network import require_network_view
from routers.permissions import require_admin

router = APIRouter()
log = logging.getLogger(__name__)


def _smartolt_failed(e):
    raise HTTPException(status_code=502, detail=f"{e}")


@router.get("/olt/onus")
def configured_onus(current_user: dict = Depends(require_network_view)):
    # Loading the page also starts any poll that's due, so the list stays
    # fresh while the router isn't reporting.
    smartolt.poll_if_due()
    try:
        data = smartolt.configured_list()
    except pg_errors.UndefinedColumn:
        raise HTTPException(status_code=503, detail="The OLT tables aren't set up yet (migration 0032).")
    except pg_errors.UndefinedTable:
        raise HTTPException(status_code=503, detail="The OLT tables aren't set up yet (migrations 0031 and 0032).")
    return {**data, "can_authorize": current_user["role"] == "admin"}


@router.get("/olt/onus/{onu_id}")
def onu_details(onu_id: str, current_user: dict = Depends(require_network_view)):
    try:
        return smartolt.onu_details(onu_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Unknown ONU")
    except smartolt.SmartOLTError as e:
        _smartolt_failed(e)


@router.get("/olt/onus/{onu_id}/status")
def onu_status(onu_id: str, current_user: dict = Depends(require_network_view)):
    try:
        return smartolt.onu_live_status(onu_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Unknown ONU")
    except smartolt.SmartOLTError as e:
        _smartolt_failed(e)


@router.get("/olt/onus/{onu_id}/graph/{kind}/{period}")
def onu_graph(onu_id: str, kind: str, period: str, current_user: dict = Depends(require_network_view)):
    try:
        png = smartolt.onu_graph(onu_id, kind, period)
    except ValueError:
        raise HTTPException(status_code=404, detail="Unknown graph")
    except smartolt.SmartOLTError as e:
        _smartolt_failed(e)
    return Response(content=png, media_type="image/png", headers={"Cache-Control": "private, max-age=300"})


@router.get("/olt/unconfigured")
def unconfigured_onus(current_user: dict = Depends(require_network_view)):
    try:
        rows = smartolt.unconfigured()
    except smartolt.SmartOLTError as e:
        _smartolt_failed(e)
    return {"onus": rows, "can_authorize": current_user["role"] == "admin", "writes_enabled": smartolt.writes_enabled()}


@router.get("/olt/authorize-options")
def authorize_options(olt_id: int = 1, pon_type: str = "gpon", current_user: dict = Depends(require_admin)):
    try:
        return smartolt.authorize_options(olt_id, pon_type)
    except smartolt.SmartOLTError as e:
        _smartolt_failed(e)


@router.get("/olt/zones/{zone_id}/odbs")
def zone_odbs(zone_id: int, current_user: dict = Depends(require_admin)):
    try:
        return {"odbs": smartolt.odbs(zone_id)}
    except smartolt.SmartOLTError as e:
        _smartolt_failed(e)


# SmartOLT's own character rules for these fields (openapi.json).
NAME_CHARS = re.compile(r"^[A-Za-z0-9 @#$&()\-`.+,/_]*$")
ZONE_CHARS = re.compile(r"^[A-Za-z0-9 _\-]*$")
PROFILE_CHARS = re.compile(r"^[A-Za-z0-9 _.\-/]*$")


class AuthorizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    olt_id: int = Field(ge=1)
    pon_type: Literal["gpon", "epon"]
    sn: str = Field(min_length=4, max_length=40, pattern=r"^[A-Za-z0-9:]+$")
    board: int = Field(ge=0, le=64)
    port: int = Field(ge=0, le=256)
    onu_type: str = Field(min_length=1, max_length=64)
    onu_mode: Literal["Routing", "Bridging"]
    vlan: Optional[int] = Field(default=None, ge=1, le=4094)
    zone: str = Field(min_length=1, max_length=64)
    odb: Optional[str] = Field(default=None, max_length=64)
    name: str = Field(min_length=1, max_length=80)
    address_or_comment: Optional[str] = Field(default=None, max_length=120)
    onu_external_id: Optional[str] = Field(default=None, max_length=40, pattern=r"^[A-Za-z0-9]*$")
    upload_speed_profile_name: Optional[str] = Field(default=None, max_length=40)
    download_speed_profile_name: Optional[str] = Field(default=None, max_length=40)

    @field_validator("name", "address_or_comment")
    @classmethod
    def _name_chars(cls, v):
        if v and not NAME_CHARS.match(v):
            raise ValueError("only letters, numbers, spaces and @#$&()-`.+,/_ are allowed")
        return v

    @field_validator("zone", "odb")
    @classmethod
    def _zone_chars(cls, v):
        if v and not ZONE_CHARS.match(v):
            raise ValueError("only letters, numbers, spaces, - and _ are allowed")
        return v

    @field_validator("onu_type", "upload_speed_profile_name", "download_speed_profile_name")
    @classmethod
    def _profile_chars(cls, v):
        if v and not PROFILE_CHARS.match(v):
            raise ValueError("unexpected characters")
        return v


@router.post("/olt/authorize")
def authorize(body: AuthorizeRequest, current_user: dict = Depends(require_admin)):
    try:
        return smartolt.authorize(body.model_dump(), current_user["id"])
    except smartolt.WritesDisabled as e:
        raise HTTPException(status_code=403, detail=str(e))
    except smartolt.SmartOLTError as e:
        _smartolt_failed(e)


@router.get("/olt/actions")
def recent_actions(current_user: dict = Depends(require_network_view)):
    return {"actions": smartolt.recent_actions()}
