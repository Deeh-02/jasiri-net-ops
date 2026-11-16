"""Finance (Phase 7): income, expenses, profit and equipment value.

Two permissions, both in Roles > Finance, checked here on every endpoint (the
sidebar hiding a page is only cosmetic):
  finance:view             see every Finance page and figure
  finance:manage_expenses  add, edit and void expenses, edit supplier rules,
                           and upload the PPPoE recharge export
  finance:manage_assets    set asset purchase dates, lives and links, and the
                           monthly income target
The built-in admin passes both, like everywhere else.

Adding expenses is two calls with the same pasted text: /read shows what the
app understood, /save stores what the user confirmed. /save reads the text
again itself; the browser only sends choices (category, type, skip), so an
amount, code or date can only ever come from the message.
"""
from datetime import date
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

import logging

from fastapi.responses import Response

from db import deepseek, finance_ai, finance_assets, finance_expenses, finance_income, finance_logic, finance_messages, finance_performance
from db import finance_pppoe, pppoe_recharge_logic
from db.customer_import import ImportFileError
from db.connection import now_eat
from routers.auth import get_current_user
from routers.permissions import user_has_permission

router = APIRouter()
log = logging.getLogger(__name__)

NOT_SET_UP = ("The Finance tables aren't set up on this database yet. "
              "Run migrations/0037_finance_expenses.sql, then reload.")


def require_finance_view(current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "finance", "view"):
        raise HTTPException(status_code=403, detail="You don't have permission to see Finance")
    return current_user


def require_expense_manager(current_user: dict = Depends(require_finance_view)):
    if not user_has_permission(current_user, "finance", "manage_expenses"):
        raise HTTPException(status_code=403, detail="You don't have permission to add or change expenses")
    return current_user


def _categories():
    try:
        return finance_expenses.list_categories(include_retired=True)
    except finance_expenses.FinanceNotSetUp:
        raise HTTPException(status_code=503, detail=NOT_SET_UP)


def _plain(value):
    """Decimal and date as text for JSON, recursively."""
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    return value


@router.get("/finance/categories")
def get_categories(include_retired: bool = False, current_user: dict = Depends(require_finance_view)):
    try:
        return finance_expenses.list_categories(include_retired)
    except finance_expenses.FinanceNotSetUp:
        raise HTTPException(status_code=503, detail=NOT_SET_UP)


# ---- Expenses: list, edit, void ----

@router.get("/finance/expenses")
def get_expenses(month: str, include_voided: bool = False, current_user: dict = Depends(require_finance_view)):
    try:
        first_day, last_day = finance_logic.month_bounds(month)
    except ValueError:
        raise HTTPException(status_code=400, detail="Month must look like 2026-11")
    try:
        rows = finance_expenses.list_expenses(first_day, last_day, include_voided=True)
    except finance_expenses.FinanceNotSetUp:
        raise HTTPException(status_code=503, detail=NOT_SET_UP)
    summary = finance_logic.summarise_expenses(rows)
    shown = rows if include_voided else [r for r in rows if not r["voided_at"]]
    return _plain({"month": month, "rows": shown, "summary": summary})


class ExpenseEdit(BaseModel):
    payee: str | None = Field(None, max_length=120)
    category_id: int
    kind: Literal["operating", "capital"]
    segment: Literal["pppoe", "hotspot", "shared"] | None = None
    description: str | None = Field(None, max_length=300)


@router.patch("/finance/expenses/{expense_id}")
def edit_expense(expense_id: int, body: ExpenseEdit, current_user: dict = Depends(require_expense_manager)):
    active_ids = {c["id"] for c in _categories() if c["is_active"]}
    if body.category_id not in active_ids:
        raise HTTPException(status_code=400, detail="Pick a category from the list")
    changes = body.model_dump()
    changes["description"] = (changes["description"] or "").strip() or None
    changes["payee"] = (changes["payee"] or "").strip() or None
    if not finance_expenses.update_expense(expense_id, changes, current_user["id"]):
        raise HTTPException(status_code=404, detail="That expense doesn't exist or has been voided")
    return _plain(finance_expenses.get_expense(expense_id))


class VoidRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=300)


@router.post("/finance/expenses/{expense_id}/void")
def void_expense(expense_id: int, body: VoidRequest, current_user: dict = Depends(require_expense_manager)):
    if not finance_expenses.void_expense(expense_id, body.reason.strip(), current_user["id"]):
        raise HTTPException(status_code=404, detail="That expense doesn't exist or is already voided")
    return {"id": expense_id, "voided": True}


# ---- Expenses: paste, review, save ----

class PastedExpenses(BaseModel):
    messages: str = Field("", max_length=60000)
    cash: str = Field("", max_length=5000)
    cash_date: date | None = None


class AiReading(BaseModel):
    """Where the AI says the amount, date and code are in a message the app
    couldn't read. Checked against the message again on save; never trusted."""
    amount_text: str = Field(max_length=40)
    date_text: str = Field(max_length=40)
    code: str = Field(max_length=40)
    payee: str | None = Field(None, max_length=120)


class ExpenseChoice(BaseModel):
    category_id: int | None = None
    kind: Literal["operating", "capital"] = "operating"
    segment: Literal["pppoe", "hotspot", "shared"] | None = None
    description: str | None = Field(None, max_length=300)
    payee: str | None = Field(None, max_length=120)
    skip: bool = False
    remember: bool = False
    ai_suggested: bool = False
    ai_reading: AiReading | None = None


class SuggestExpenses(PastedExpenses):
    note: str = Field("", max_length=finance_ai.MAX_NOTE_LENGTH)


class SaveExpenses(PastedExpenses):
    choices: dict[str, ExpenseChoice]


def _review(body: PastedExpenses):
    """Reads the paste and the cash lines, then marks each item against the
    saved rules and codes. The same function serves /read and /save, so what
    is saved is exactly what was reviewed."""
    today = now_eat().date()
    if body.cash_date and body.cash_date > today:
        raise HTTPException(status_code=400, detail="The cash date can't be in the future")
    categories = _categories()
    message_items = finance_messages.read_messages(body.messages, today)
    cash_items = finance_messages.read_cash_lines(body.cash)
    existing = finance_expenses.existing_references([i.get("reference") for i in message_items])
    review = finance_logic.build_review(message_items, cash_items, finance_expenses.load_rules(), existing, categories)
    return review, categories, body.cash_date or today


@router.get("/finance/settings")
def get_settings(current_user: dict = Depends(require_finance_view)):
    return {"ai_enabled": finance_ai.is_enabled()}


# What the page says when DeepSeek can't help this time. The review works
# without it either way.
AI_UNAVAILABLE = {
    "timeout": "AI suggestions took too long this time.",
    "rate_limited": "AI suggestions are busy right now.",
    "auth": "AI suggestions are off: the DeepSeek key or balance needs checking.",
    "not_configured": "AI suggestions are off: no DeepSeek key is set.",
}


@router.post("/finance/expenses/suggest")
def suggest_expenses(body: SuggestExpenses, current_user: dict = Depends(require_expense_manager)):
    """✨ suggestions for the review on screen: categories for new business
    payees, readings of messages the app couldn't read. Never saves anything."""
    if not finance_ai.is_enabled():
        return {"enabled": False}
    review, categories, _ = _review(body)
    try:
        result = finance_ai.suggest(review, body.note, categories, now_eat().date())
    except deepseek.DeepSeekError as failure:
        log.warning("Finance AI suggestions failed: %s", failure.kind)
        return {"enabled": True, "error": AI_UNAVAILABLE.get(failure.kind, "AI suggestions aren't available right now.")}
    except finance_ai.BadReply:
        log.warning("Finance AI suggestions: DeepSeek's reply wasn't the JSON asked for")
        return {"enabled": True, "error": "AI suggestions came back garbled this time."}
    return _plain(dict(result, enabled=True))


@router.post("/finance/expenses/read")
def read_expenses(body: PastedExpenses, current_user: dict = Depends(require_expense_manager)):
    review, categories, cash_date = _review(body)
    if not review:
        raise HTTPException(status_code=400, detail="Paste at least one message or type a cash line")
    return _plain({"items": review, "categories": categories, "cash_date": cash_date})


@router.post("/finance/expenses/save")
def save_expenses(body: SaveExpenses, current_user: dict = Depends(require_expense_manager)):
    review, categories, cash_date = _review(body)
    # exclude_unset: a payee the page didn't send means "use the message's",
    # one sent empty means "leave blank".
    choices = {key: choice.model_dump(exclude_unset=True) for key, choice in body.choices.items()}
    review, problems = finance_ai.apply_readings(review, choices, now_eat().date())
    if problems:
        key, why = next(iter(problems.items()))
        raise HTTPException(status_code=400, detail=f"The AI reading of {key} didn't check out: {why}")
    # A code the AI found may already be saved: skip it like any duplicate.
    ai_read = [i for i in review if i.get("ai_read")]
    already = finance_expenses.existing_references([i["reference"] for i in ai_read])
    review = [dict(i, status="duplicate") if i.get("ai_read") and i["reference"] in already else i for i in review]
    try:
        rows, rules = finance_logic.build_rows_to_save(review, choices, categories, cash_date)
    except finance_logic.ChoiceError as problem:
        raise HTTPException(status_code=400, detail=str(problem))
    if not rows:
        raise HTTPException(status_code=400, detail="Nothing to save: every row is skipped or already saved")
    result = finance_expenses.save_expenses(rows, rules, current_user["id"])
    saved_total = finance_logic.total_amount([r for r in rows if r["reference"] not in result["skipped_references"]])
    return _plain(dict(result, total=saved_total, rules_saved=len(rules)))


# ---- Income (Phase 7C) ----

Segment = Literal["all", "pppoe", "hotspot"]


def _month(month: str):
    try:
        return finance_logic.month_bounds(month)[0]
    except ValueError:
        raise HTTPException(status_code=400, detail="Month must look like 2026-11")


@router.get("/finance/income")
def get_income(month: str, segment: Segment = "all", current_user: dict = Depends(require_finance_view)):
    return _plain(finance_income.income_report(_month(month), segment, now_eat().date()))


@router.get("/finance/income/transactions")
def get_income_transactions(month: str, segment: Segment = "all", search: str | None = None,
                            day: date | None = None, package: str | None = None, page: int = 1,
                            current_user: dict = Depends(require_finance_view)):
    search = (search or "").strip()[:60] or None
    return _plain(finance_income.transactions(_month(month), segment, now_eat().date(), search=search,
                                              day=day, package=package, page=max(1, page)))


@router.get("/finance/income/transactions.csv")
def export_income_transactions(month: str, segment: Segment = "all", search: str | None = None,
                               day: date | None = None, package: str | None = None,
                               current_user: dict = Depends(require_finance_view)):
    search = (search or "").strip()[:60] or None
    text = finance_income.transactions_csv(_month(month), segment, now_eat().date(), search=search,
                                           day=day, package=package)
    return Response(content=text, media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="income-{month}-{segment}.csv"'})


# ---- PPPoE recharges (the PPPoE income source, owner's choice 2026-11-16) ----

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
PPPOE_NOT_SET_UP = "PPPoE income isn't set up on this database yet. Run migrations/0039_pppoe_recharges.sql, then reload."


def require_recharge_uploader(current_user: dict = Depends(require_finance_view)):
    if not user_has_permission(current_user, "finance", "manage_expenses"):
        raise HTTPException(status_code=403, detail="You don't have permission to upload PPPoE recharges")
    return current_user


@router.get("/finance/pppoe/status")
def get_pppoe_status(current_user: dict = Depends(require_finance_view)):
    try:
        return _plain(finance_pppoe.status())
    except finance_pppoe.PppoeNotSetUp:
        raise HTTPException(status_code=503, detail=PPPOE_NOT_SET_UP)


@router.post("/finance/pppoe/import")
async def import_pppoe_recharges(commit: bool = Form(False), file: UploadFile = File(...),
                                 current_user: dict = Depends(require_recharge_uploader)):
    """Dry run unless commit=true: the same call, so what the preview showed
    is exactly what a save does. Only recharges not saved before are added."""
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="That file is over 5 MB. Is it the right one?")
    try:
        parsed = pppoe_recharge_logic.parse_recharge_file(file.filename, content)
    except ImportFileError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not parsed["rows"]:
        raise HTTPException(status_code=400, detail="No recharges found in that file.")
    try:
        result = finance_pppoe.import_recharges(file.filename, parsed["rows"], current_user["id"], commit)
    except finance_pppoe.PppoeNotSetUp:
        raise HTTPException(status_code=503, detail=PPPOE_NOT_SET_UP)
    return _plain(dict(result, unreadable=parsed["unreadable"]))


# ---- Performance (Phase 7D) ----

@router.get("/finance/performance")
def get_performance(month: str, segment: Segment = "all", current_user: dict = Depends(require_finance_view)):
    try:
        return _plain(finance_performance.report(_month(month), segment, now_eat().date()))
    except finance_expenses.FinanceNotSetUp:
        raise HTTPException(status_code=503, detail=NOT_SET_UP)


# ---- Assets (Phase 7F) and month targets (7E) ----

def require_asset_manager(current_user: dict = Depends(require_finance_view)):
    if not user_has_permission(current_user, "finance", "manage_assets"):
        raise HTTPException(status_code=403, detail="You don't have permission to change asset values or targets")
    return current_user


@router.get("/finance/assets")
def get_assets(current_user: dict = Depends(require_finance_view)):
    try:
        return _plain(finance_assets.report(now_eat().date()))
    except finance_expenses.FinanceNotSetUp:
        raise HTTPException(status_code=503, detail="The Assets tables aren't set up yet. Run migrations/0038_finance_assets_and_targets.sql.")


@router.get("/finance/assets/{item_id}/history")
def get_asset_history(item_id: int, current_user: dict = Depends(require_finance_view)):
    return _plain(finance_assets.item_history(item_id))


class AssetValues(BaseModel):
    purchased_on: date | None = None
    warranty_until: date | None = None
    segment: Literal["pppoe", "hotspot", "shared"] | None = None
    expense_id: int | None = None


@router.patch("/finance/assets/{item_id}")
def edit_asset(item_id: int, body: AssetValues, current_user: dict = Depends(require_asset_manager)):
    if not finance_assets.is_asset(item_id):
        raise HTTPException(status_code=404, detail="That isn't a serialised Inventory item")
    changes = body.model_dump(exclude_unset=True)
    if changes.get("purchased_on") and changes["purchased_on"] > now_eat().date():
        raise HTTPException(status_code=400, detail="The purchase date can't be in the future")
    if changes.get("expense_id") and not finance_assets.is_unlinked_capital_expense(changes["expense_id"], item_id):
        raise HTTPException(status_code=400, detail="Pick a capital expense that isn't linked to another asset")
    finance_assets.set_values([item_id], changes, current_user["id"])
    return {"id": item_id, "saved": True}


class CategoryDate(BaseModel):
    # Given to every item in the category that has no purchase date yet;
    # dates already set stay as they are.
    purchased_on: date


@router.put("/finance/assets/categories/{category_id}/purchase-date")
def date_undated_in_category(category_id: int, body: CategoryDate, current_user: dict = Depends(require_asset_manager)):
    if body.purchased_on > now_eat().date():
        raise HTTPException(status_code=400, detail="The purchase date can't be in the future")
    undated = finance_assets.category_items_without_date(category_id)
    dated = finance_assets.set_values(undated, {"purchased_on": body.purchased_on}, current_user["id"])
    return {"category_id": category_id, "dated": dated}


class MonthTarget(BaseModel):
    amount: Decimal | None = Field(None, gt=0, max_digits=12, decimal_places=2)


@router.put("/finance/targets/{month}")
def set_month_target(month: str, body: MonthTarget, current_user: dict = Depends(require_asset_manager)):
    finance_assets.set_target(_month(month), body.amount, current_user["id"])
    return {"month": month, "amount": str(body.amount) if body.amount is not None else None}
