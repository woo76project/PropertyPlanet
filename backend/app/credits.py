from uuid import UUID
from typing import Literal

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Path
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field

from app.auth import require_admin, require_complete_profile
from app.audit import record_audit
from app.core.config import get_settings

router = APIRouter(tags=["Search credits"])


class CreditAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    request_id: UUID
    amount: int = Field(gt=0, le=1000000, strict=True)
    reference: str = Field(min_length=1, max_length=200)


class CreditAdjustment(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    request_id: UUID
    direction: Literal["add", "subtract"]
    amount: int = Field(gt=0, le=1000000, strict=True)
    reason: str = Field(min_length=1, max_length=200)
    expected_balance: int = Field(ge=0, le=1000000000, strict=True)


def balance_for(connection, user_id):
    row = connection.execute("SELECT credit_balance AS balance FROM users WHERE id = %s", (user_id,)).fetchone()
    return row["balance"] if row else 0


@router.get("/credits")
def my_credits(user: dict = Depends(require_complete_profile)):
    settings = get_settings()
    if user["role"] == "Admin":
        return {"balance": 0, "unlimited": True}
    with psycopg.connect(settings.db_url.get_secret_value(), row_factory=dict_row, connect_timeout=15) as connection:
        balance = balance_for(connection, user["id"])
    return {"balance": balance, "unlimited": False}


@router.get("/admin/users/{user_id}/credits")
def user_credits(user_id: int = Path(gt=0), admin: dict = Depends(require_admin)):
    with psycopg.connect(get_settings().db_url.get_secret_value(), row_factory=dict_row, connect_timeout=15) as connection:
        user = connection.execute("SELECT id, role, status FROM users WHERE id = %s", (user_id,)).fetchone()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        return {"balance": 0 if user["role"] == "Admin" else balance_for(connection, user_id), "unlimited": user["role"] == "Admin"}


@router.post("/admin/users/{user_id}/credits")
def assign_credits(data: CreditAssignment, user_id: int = Path(gt=0), admin: dict = Depends(require_admin)):
    with psycopg.connect(get_settings().db_url.get_secret_value(), row_factory=dict_row, connect_timeout=15) as connection:
        # Same lock order as status changes, deletion and credit use.
        user = connection.execute("SELECT id, role, status FROM users WHERE id = %s FOR UPDATE", (user_id,)).fetchone()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        if user["role"] != "User" or user["status"] != "Active":
            raise HTTPException(status_code=409, detail="Credits can only be assigned to an Active User account")
        prior = connection.execute("SELECT * FROM credit_assignments WHERE request_id = %s", (data.request_id,)).fetchone()
        if prior:
            if (prior["user_id"], prior["actor_user_id"], prior["amount"], prior["reference"]) != (user_id, admin["id"], data.amount, data.reference):
                raise HTTPException(status_code=409, detail="Assignment reference was already used for another request")
            return {"balance": balance_for(connection, user_id), "assigned": data.amount, "duplicate": True}
        updated = connection.execute(
            "UPDATE users SET credit_balance = credit_balance + %s, updated_at = NOW() "
            "WHERE id = %s AND credit_balance <= 1000000000 - %s RETURNING credit_balance AS balance",
            (data.amount, user_id, data.amount),
        ).fetchone()
        if not updated:
            raise HTTPException(status_code=409, detail="This assignment would exceed the credit balance limit")
        connection.execute(
            "INSERT INTO credit_assignments (request_id, user_id, actor_user_id, amount, reference, balance_after) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (data.request_id, user_id, admin["id"], data.amount, data.reference, updated["balance"]),
        )
        record_audit(connection, admin["id"], "user.credits_assigned", "user", user_id,
                     {"amount": data.amount, "balance_after": updated["balance"], "reference": data.reference})
    return {"balance": updated["balance"], "assigned": data.amount, "duplicate": False}


@router.post("/admin/users/{user_id}/credits/adjustments")
def adjust_credits(data: CreditAdjustment, user_id: int = Path(gt=0), admin: dict = Depends(require_admin)):
    delta = data.amount if data.direction == "add" else -data.amount
    with psycopg.connect(get_settings().db_url.get_secret_value(), row_factory=dict_row, connect_timeout=15) as connection:
        user = connection.execute(
            "SELECT id, role, status, credit_balance FROM users WHERE id = %s FOR UPDATE", (user_id,),
        ).fetchone()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        if user["role"] != "User" or user["status"] != "Active":
            raise HTTPException(status_code=409, detail="Credits can only be adjusted for an Active User account")
        prior = connection.execute("SELECT * FROM credit_adjustments WHERE request_id = %s", (data.request_id,)).fetchone()
        if prior:
            if (prior["user_id"], prior["actor_user_id"], prior["delta"], prior["reason"], prior["balance_before"]) != (
                user_id, admin["id"], delta, data.reason, data.expected_balance,
            ):
                raise HTTPException(status_code=409, detail="This adjustment request ID was already used for a different change")
            return {"balance": user["credit_balance"], "adjusted": delta, "duplicate": True}
        before = user["credit_balance"]
        if before != data.expected_balance:
            raise HTTPException(status_code=409, detail="Balance changed. Refresh the credit balance and review the adjustment again.")
        after = before + delta
        if not 0 <= after <= 1000000000:
            raise HTTPException(status_code=409, detail="Adjustment must keep the balance between 0 and 1,000,000,000 credits")
        connection.execute("UPDATE users SET credit_balance = credit_balance + %s, updated_at = NOW() WHERE id = %s", (delta, user_id))
        connection.execute(
            "INSERT INTO credit_adjustments (request_id, user_id, actor_user_id, delta, reason, balance_before, balance_after) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (data.request_id, user_id, admin["id"], delta, data.reason, before, after),
        )
        record_audit(connection, admin["id"], "user.credits_adjusted", "user", user_id,
                     {"delta": delta, "reason": data.reason, "balance_before": before, "balance_after": after})
    return {"balance": after, "adjusted": delta, "duplicate": False}
