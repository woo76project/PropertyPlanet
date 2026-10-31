import httpx
import psycopg
from clerk_backend_api import Clerk
from clerk_backend_api.models import ClerkBaseError
from fastapi import APIRouter, Depends, HTTPException, Response
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict
from typing import Literal

from app.auth import require_clerk_user
from app.core.config import get_settings

router = APIRouter(prefix="/auth", tags=["Authentication"])


class AccountDeletion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmation: Literal["DELETE"]


def delete_user_account(clerk_user_id, settings):
    """Block access and retire credits before deleting the external identity."""
    if not settings.clerk_secret_key or not settings.clerk_secret_key.get_secret_value():
        raise HTTPException(status_code=503, detail="Clerk is not configured yet")

    try:
        with psycopg.connect(
            settings.db_url.get_secret_value(), row_factory=dict_row, connect_timeout=15,
        ) as connection:
            # Also supports users who have not created their application profile yet.
            # The unique Clerk ID serializes a concurrent profile-creation request.
            connection.execute(
                "INSERT INTO users (clerk_user_id, email, status) VALUES (%s, %s, 'Deleting') "
                "ON CONFLICT (clerk_user_id) DO NOTHING",
                (clerk_user_id, f"deleted-{clerk_user_id}@accounts.invalid"),
            )
            user = connection.execute(
                "SELECT id, role, status FROM users WHERE clerk_user_id = %s FOR UPDATE",
                (clerk_user_id,),
            ).fetchone()
            if user["role"] == "Admin":
                raise HTTPException(status_code=403, detail="Admin accounts require owner-managed deletion")
            if user["status"] == "Deleted":
                return

            connection.execute(
                "UPDATE users SET status = 'Deleting', credit_balance = 0, updated_at = NOW() WHERE id = %s",
                (user["id"],),
            )
        # Commit the blocked state before touching Clerk. If the process stops here,
        # retry_account_deletions can finish without the user's session token.
        try:
            with Clerk(bearer_auth=settings.clerk_secret_key.get_secret_value()) as clerk:
                result = clerk.users.delete(user_id=clerk_user_id, timeout_ms=15000, retries=None)
                if not result.deleted:
                    raise HTTPException(status_code=503, detail="Account deletion is pending; retry later")
        except ClerkBaseError as error:
            # A retry after successful Clerk deletion receives 404; finish Neon cleanup.
            if error.status_code != 404:
                raise HTTPException(status_code=503, detail="Account deletion is pending; retry later") from None
        except httpx.HTTPError:
            raise HTTPException(status_code=503, detail="Account deletion is pending; retry later") from None

        with psycopg.connect(
            settings.db_url.get_secret_value(), row_factory=dict_row, connect_timeout=15,
        ) as connection:
            connection.execute(
                "UPDATE users SET username = NULL, email = %s, first_name = NULL, "
                "last_name = NULL, phone = NULL, status = 'Deleted', updated_at = NOW() "
                "WHERE id = %s AND status = 'Deleting'",
                (f"deleted-{clerk_user_id}@accounts.invalid", user["id"]),
            )
    except psycopg.Error:
        raise HTTPException(status_code=503, detail="Account deletion could not finish; retry later") from None


@router.get("/account")
def account_deletion_state(clerk_user_id: str = Depends(require_clerk_user)):
    try:
        with psycopg.connect(
            get_settings().db_url.get_secret_value(), row_factory=dict_row, connect_timeout=15,
        ) as connection:
            user = connection.execute(
                "SELECT role, status FROM users WHERE clerk_user_id = %s", (clerk_user_id,),
            ).fetchone()
    except psycopg.Error:
        raise HTTPException(status_code=503, detail="Account settings are temporarily unavailable") from None
    return {"can_delete": user is None or user["role"] != "Admin", "status": user["status"] if user else "NoProfile"}


@router.delete("/account", status_code=204)
def delete_account(data: AccountDeletion, clerk_user_id: str = Depends(require_clerk_user)):
    # No target user ID is accepted: users can only delete their own account.
    # Suspended/incomplete profiles may delete too, so do not require an active profile.
    delete_user_account(clerk_user_id, get_settings())
    return Response(status_code=204)
