from typing import Literal

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Path, Query
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict

from app.auth import require_admin
from app.audit import record_audit
from app.core.config import get_settings

router = APIRouter(prefix="/admin", tags=["Admin"])


class UserStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["Active", "Suspended"]


@router.get("/users")
def list_users(
    search: str | None = Query(default=None, max_length=200),
    status: Literal["Active", "Suspended"] | None = None,
    before_id: int | None = Query(default=None, gt=0),
    limit: int = Query(default=20, ge=1, le=100),
    admin: dict = Depends(require_admin),
):
    conditions = []
    values = []
    if search and search.strip():
        # Treat LIKE wildcards in the search as literal characters.
        term = search.strip().replace("!", "!!").replace("%", "!%").replace("_", "!_")
        conditions.append(
            "(username ILIKE %s ESCAPE '!' OR email ILIKE %s ESCAPE '!' "
            "OR first_name ILIKE %s ESCAPE '!' OR last_name ILIKE %s ESCAPE '!' "
            "OR phone ILIKE %s ESCAPE '!')"
        )
        values.extend([f"%{term}%"] * 5)
    if status:
        conditions.append("status = %s")
        values.append(status)
    if before_id is not None:
        conditions.append("id < %s")
        values.append(before_id)

    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    query = (
        "SELECT id, username, email, first_name, last_name, phone, role, status, "
        "created_at, updated_at, credit_balance AS credits "
        "FROM users" + where + " ORDER BY id DESC LIMIT %s"
    )
    settings = get_settings()
    try:
        with psycopg.connect(
            settings.db_url.get_secret_value(), row_factory=dict_row, connect_timeout=15
        ) as connection:
            # Fetch one extra user to detect whether another page exists.
            rows = connection.execute(query, (*values, limit + 1)).fetchall()
    except psycopg.Error:
        raise HTTPException(status_code=503, detail="User list is temporarily unavailable") from None

    has_more = len(rows) > limit
    results = rows[:limit]
    return {
        "users": results,
        "pagination": {
            "limit": limit,
            "has_more": has_more,
            "next_before_id": results[-1]["id"] if has_more else None,
        },
    }


@router.patch("/users/{user_id}/status")
def update_user_status(
    data: UserStatusUpdate,
    user_id: int = Path(gt=0),
    admin: dict = Depends(require_admin),
):
    settings = get_settings()
    try:
        with psycopg.connect(
            settings.db_url.get_secret_value(), row_factory=dict_row, connect_timeout=15
        ) as connection:
            # Lock the target until the role check and status update finish.
            user = connection.execute(
                "SELECT id, username, email, first_name, last_name, phone, role, status, "
                "created_at, updated_at FROM users WHERE id = %s FOR UPDATE",
                (user_id,),
            ).fetchone()
            if not user:
                raise HTTPException(status_code=404, detail="User not found")
            if user["role"] == "Admin":
                raise HTTPException(status_code=403, detail="Admin accounts cannot be changed here")
            if user["status"] in ("Deleting", "Deleted"):
                raise HTTPException(status_code=409, detail="An account being deleted cannot be reactivated or suspended")
            if user["status"] == data.status:
                return user
            previous_status = user["status"]

            user = connection.execute(
                "UPDATE users SET status = %s, updated_at = NOW() "
                "WHERE id = %s AND role = 'User' "
                "RETURNING id, username, email, first_name, last_name, phone, role, status, "
                "created_at, updated_at",
                (data.status, user_id),
            ).fetchone()
            record_audit(
                connection, admin["id"],
                "user.suspended" if data.status == "Suspended" else "user.reactivated",
                "user", user_id, {"previous_status": previous_status, "status": data.status},
            )
    except psycopg.Error:
        raise HTTPException(status_code=503, detail="User status update is temporarily unavailable") from None

    return user
