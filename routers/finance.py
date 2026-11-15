"""Finance (Phase 7): income, expenses, profit and equipment value.

Two permissions, both in Roles > Finance, checked here on every endpoint (the
sidebar hiding a page is only cosmetic):
  finance:view             see every Finance page and figure
  finance:manage_expenses  add, edit and void expenses, and edit supplier rules
The built-in admin passes both, like everywhere else.

Adding expenses is two calls with the same pasted text: /read shows what the
app understood, /save stores what the user confirmed. /save reads the text
again itself; the browser only sends choices (category, type, skip), so an
amount, code or date can only ever come from the message.
"""
from datetime import date
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from db import finance_expenses, finance_logic, finance_messages
from db.connection import now_eat
from routers.auth import get_current_user
from routers.permissions import user_has_permission

router = APIRouter()

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


class ExpenseChoice(BaseModel):
    category_id: int | None = None
    kind: Literal["operating", "capital"] = "operating"
    segment: Literal["pppoe", "hotspot", "shared"] | None = None
    description: str | None = Field(None, max_length=300)
    payee: str | None = Field(None, max_length=120)
    skip: bool = False
    remember: bool = False


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
    try:
        rows, rules = finance_logic.build_rows_to_save(review, choices, categories, cash_date)
    except finance_logic.ChoiceError as problem:
        raise HTTPException(status_code=400, detail=str(problem))
    if not rows:
        raise HTTPException(status_code=400, detail="Nothing to save: every row is skipped or already saved")
    result = finance_expenses.save_expenses(rows, rules, current_user["id"])
    saved_total = finance_logic.total_amount([r for r in rows if r["reference"] not in result["skipped_references"]])
    return _plain(dict(result, total=saved_total, rules_saved=len(rules)))
