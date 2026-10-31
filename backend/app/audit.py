from typing import Literal

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.auth import require_admin
from app.core.config import get_settings

router = APIRouter(prefix="/admin", tags=["Audit history"])

AuditAction = Literal[
    "property.created", "property.updated", "property.deleted",
    "user.suspended", "user.reactivated", "properties.imported", "properties.exported",
    "user.credits_assigned",
    "user.credits_adjusted",
]


def record_audit(connection, actor_user_id: int, action: AuditAction,
                 entity_type: str, entity_id: int | None = None, details: dict | None = None):
    # Use the caller's transaction: do not open or commit a separate connection.
    connection.execute(
        "INSERT INTO audit_logs (actor_user_id, action, entity_type, entity_id, details) "
        "VALUES (%s, %s, %s, %s, %s)",
        (actor_user_id, action, entity_type, entity_id, Jsonb(details or {})),
    )


@router.get("/audit-logs")
def list_audit_logs(
    action: AuditAction | None = None,
    category: Literal["properties", "users"] | None = None,
    actor_user_id: int | None = Query(default=None, gt=0),
    actor_username: str | None = Query(default=None, max_length=200),
    before_id: int | None = Query(default=None, gt=0),
    limit: int = Query(default=20, ge=1, le=100),
    admin: dict = Depends(require_admin),
):
    conditions = []
    values = []
    if category:
        conditions.append("a.entity_type = %s")
        values.append("property" if category == "properties" else "user")
    if action:
        conditions.append("a.action = %s")
        values.append(action)
    if actor_user_id is not None:
        conditions.append("a.actor_user_id = %s")
        values.append(actor_user_id)
    if actor_username and actor_username.strip():
        # Literal, case-insensitive substring search: % and _ are not wildcards.
        conditions.append("strpos(lower(u.username), lower(%s)) > 0")
        values.append(actor_username.strip())
    if before_id is not None:
        conditions.append("a.id < %s")
        values.append(before_id)
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    query = (
        "SELECT a.id, a.actor_user_id, u.username AS actor_username, a.action, "
        "a.entity_type, a.entity_id, a.details, a.created_at "
        "FROM audit_logs a JOIN users u ON u.id = a.actor_user_id" + where +
        " ORDER BY a.id DESC LIMIT %s"
    )
    settings = get_settings()
    try:
        with psycopg.connect(settings.db_url.get_secret_value(), row_factory=dict_row, connect_timeout=15) as connection:
            rows = connection.execute(query, (*values, limit + 1)).fetchall()
    except psycopg.Error:
        raise HTTPException(status_code=503, detail="Audit history is temporarily unavailable") from None
    has_more = len(rows) > limit
    results = rows[:limit]
    return {
        "audit_logs": results,
        "pagination": {
            "limit": limit, "has_more": has_more,
            "next_before_id": results[-1]["id"] if has_more else None,
        },
    }
