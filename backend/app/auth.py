import httpx
import re
import logging
from enum import Enum
import phonenumbers
import psycopg
from psycopg.rows import dict_row
from clerk_backend_api import Clerk, AuthenticateRequestOptions, authenticate_request
from clerk_backend_api.models import ClerkBaseError
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, ConfigDict, field_validator

from app.core.config import get_settings

router = APIRouter(prefix="/auth", tags=["Authentication"])


class ProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    username: str = Field(min_length=4, max_length=50, pattern=r"^[a-zA-Z0-9_]+$")
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    phone: str = Field(min_length=1, max_length=30)

    @field_validator("phone")
    @classmethod
    def standardize_phone(cls, value: str) -> str:
        # Country selection happens in the form. Never guess a country on the API.
        if not re.fullmatch(r"\+[0-9 ()-]+", value):
            raise ValueError("Choose a country and enter a valid phone number")
        try:
            number = phonenumbers.parse(value, None)
        except phonenumbers.NumberParseException:
            raise ValueError("Choose a country and enter a valid phone number") from None
        if not phonenumbers.is_valid_number(number):
            raise ValueError("Choose a country and enter a valid phone number")
        return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)


def clerk_profile(settings, clerk_user_id):
    if not settings.clerk_secret_key or not settings.clerk_secret_key.get_secret_value():
        raise HTTPException(status_code=503, detail="Clerk is not configured yet")
    try:
        with Clerk(bearer_auth=settings.clerk_secret_key.get_secret_value()) as clerk:
            user = clerk.users.get(user_id=clerk_user_id, timeout_ms=15000, retries=None)
    except (ClerkBaseError, httpx.HTTPError):
        raise HTTPException(status_code=503, detail="Unable to retrieve Clerk profile") from None
    if user.banned or user.locked:
        raise HTTPException(status_code=403, detail="Clerk account is blocked")
    return user


def require_clerk_user(request: Request) -> str:
    # Clerk verifies the token signature and expiry.
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer ") or not authorization[7:].strip():
        raise HTTPException(status_code=401, detail="Sign in required")

    settings = get_settings()
    if not settings.clerk_secret_key or not settings.clerk_secret_key.get_secret_value():
        raise HTTPException(status_code=503, detail="Clerk is not configured yet")

    state = authenticate_request(
        request,
        AuthenticateRequestOptions(
            secret_key=settings.clerk_secret_key.get_secret_value(),
            authorized_parties=[settings.frontend_url],
            accepts_token=["session_token"],
        ),
    )
    if not state.is_signed_in or not state.payload:
        reason = getattr(state, "reason", None)
        # SDK reason names are fixed enums. Never log JWTs, payloads or raw errors.
        logging.getLogger("uvicorn.error.auth").warning(
            "Session verification rejected method=%s reason=%s",
            request.method, reason.name if isinstance(reason, Enum) else "UNKNOWN",
        )
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    user_id = state.payload.get("sub")
    if not isinstance(user_id, str) or not user_id.startswith("user_"):
        raise HTTPException(status_code=401, detail="Invalid user session")
    return user_id


def require_complete_profile(clerk_user_id: str = Depends(require_clerk_user)) -> dict:
    # Future property endpoints can use this dependency before returning data.
    settings = get_settings()
    with psycopg.connect(
        settings.db_url.get_secret_value(), row_factory=dict_row, connect_timeout=15
    ) as connection:
        user = connection.execute(
            "SELECT id, clerk_user_id, username, email, first_name, last_name, phone, role, status "
            "FROM users WHERE clerk_user_id = %s",
            (clerk_user_id,),
        ).fetchone()

    if not user:
        raise HTTPException(status_code=404, detail="Profile not created yet")
    if user["status"] != "Active":
        raise HTTPException(status_code=403, detail="Account is not active")

    for field in ("username", "phone", "first_name", "last_name"):
        value = user.get(field)
        if not isinstance(value, str) or not value.strip():
            raise HTTPException(status_code=403, detail="Complete your profile first")
    return user


def require_admin(user: dict = Depends(require_complete_profile)) -> dict:
    # Roles come from Neon; only you assign Admin through controlled SQL.
    if user["role"] != "Admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


@router.get("/me")
def me(clerk_user_id: str = Depends(require_clerk_user)):
    settings = get_settings()
    with psycopg.connect(
        settings.db_url.get_secret_value(), row_factory=dict_row, connect_timeout=15
    ) as connection:
        user = connection.execute(
            "SELECT id, clerk_user_id, username, email, first_name, last_name, phone, role, status "
            "FROM users WHERE clerk_user_id = %s",
            (clerk_user_id,),
        ).fetchone()

    if not user:
        raise HTTPException(status_code=404, detail="Profile not created yet")
    if user["status"] != "Active":
        raise HTTPException(status_code=403, detail="Account is not active")
    return user


@router.post("/profile")
def create_profile(clerk_user_id: str = Depends(require_clerk_user)):
    settings = get_settings()
    database_url = settings.db_url.get_secret_value()

    with psycopg.connect(database_url, row_factory=dict_row, connect_timeout=15) as connection:
        user = connection.execute(
            "SELECT id, clerk_user_id, username, email, first_name, last_name, phone, role, status "
            "FROM users WHERE clerk_user_id = %s FOR UPDATE",
            (clerk_user_id,),
        ).fetchone()

        if user:
            if user["status"] != "Active":
                raise HTTPException(status_code=403, detail="Account is not active")
            # Use the same user lock as profile edits/deletion, so a stale read
            # cannot overwrite a concurrent username change made through this API.
            clerk_user = clerk_profile(settings, clerk_user_id)
            if user["username"] != clerk_user.username:
                try:
                    user = connection.execute(
                        "UPDATE users SET username = %s, updated_at = NOW() "
                        "WHERE id = %s RETURNING id, clerk_user_id, username, email, "
                        "first_name, last_name, phone, role, status",
                        (clerk_user.username, user["id"]),
                    ).fetchone()
                except psycopg.errors.UniqueViolation:
                    raise HTTPException(status_code=409, detail="Your login username conflicts with another profile; contact support") from None
            return user

    # Get identity details from Clerk, not from browser-supplied JSON.
    clerk_user = clerk_profile(settings, clerk_user_id)

    primary_email = next(
        (email for email in clerk_user.email_addresses
         if email.id == clerk_user.primary_email_address_id),
        None,
    )
    if not primary_email or not primary_email.verification or primary_email.verification.status != "verified":
        raise HTTPException(status_code=403, detail="A verified primary email is required")

    try:
        with psycopg.connect(database_url, row_factory=dict_row, connect_timeout=15) as connection:
            # A repeated or concurrent call must not create another profile.
            connection.execute(
                """
                INSERT INTO users (clerk_user_id, username, email, first_name, last_name, role, status)
                VALUES (%s, %s, %s, %s, %s, 'User', 'Active')
                ON CONFLICT (clerk_user_id) DO NOTHING
                """,
                (clerk_user_id, clerk_user.username,
                 primary_email.email_address.lower(),
                 clerk_user.first_name, clerk_user.last_name),
            )
            user = connection.execute(
                "SELECT id, clerk_user_id, username, email, first_name, last_name, phone, role, status "
                "FROM users WHERE clerk_user_id = %s",
                (clerk_user_id,),
            ).fetchone()
    except psycopg.errors.UniqueViolation:
        raise HTTPException(status_code=409, detail="Email or username already belongs to another profile") from None

    if not user:
        raise HTTPException(status_code=503, detail="Unable to create profile")
    if user["status"] != "Active":
        raise HTTPException(status_code=403, detail="Account is not active")
    return user


@router.patch("/profile")
def update_profile(data: ProfileUpdate, clerk_user_id: str = Depends(require_clerk_user)):
    settings = get_settings()
    try:
        with psycopg.connect(
            settings.db_url.get_secret_value(), row_factory=dict_row, connect_timeout=15
        ) as connection:
            user = connection.execute(
                "SELECT id, username, status FROM users WHERE clerk_user_id = %s FOR UPDATE",
                (clerk_user_id,),
            ).fetchone()
            if not user:
                raise HTTPException(status_code=404, detail="Profile not created yet")
            if user["status"] != "Active":
                raise HTTPException(status_code=403, detail="Account is not active")
            username = data.username.lower()
            conflict = connection.execute(
                "SELECT id FROM users WHERE LOWER(username) = %s AND id <> %s LIMIT 1",
                (username, user["id"]),
            ).fetchone()
            if conflict:
                raise HTTPException(status_code=409, detail="Username is already taken")

            clerk_user = clerk_profile(settings, clerk_user_id)
            if clerk_user.username != username:
                try:
                    with Clerk(bearer_auth=settings.clerk_secret_key.get_secret_value()) as clerk:
                        clerk_user = clerk.users.update(
                            user_id=clerk_user_id, username=username, timeout_ms=15000, retries=None,
                        )
                except ClerkBaseError as error:
                    errors = getattr(getattr(error, "data", None), "errors", [])
                    if any(item.code == "form_identifier_exists" for item in errors):
                        raise HTTPException(status_code=409, detail="Username is already taken") from None
                    if error.status_code in (400, 422):
                        raise HTTPException(status_code=422, detail="Username is not allowed by the sign-in settings; choose another or contact support") from None
                    raise HTTPException(status_code=503, detail="Unable to update your sign-in username; retry saving") from None
                except httpx.HTTPError:
                    raise HTTPException(status_code=503, detail="Unable to update your sign-in username; retry saving") from None
                if clerk_user.username != username:
                    raise HTTPException(status_code=503, detail="Sign-in username change was not confirmed; retry saving")
            user = connection.execute(
                """
                UPDATE users
                SET username = %s, first_name = %s, last_name = %s,
                    phone = %s, updated_at = NOW()
                WHERE clerk_user_id = %s AND status = 'Active'
                RETURNING id, clerk_user_id, username, email,
                          first_name, last_name, phone, role, status
                """,
                (clerk_user.username, data.first_name, data.last_name,
                 data.phone, clerk_user_id),
            ).fetchone()
    except psycopg.errors.UniqueViolation:
        raise HTTPException(status_code=409, detail="Username is already taken") from None
    except psycopg.Error:
        # Clerk and Neon are separate systems; external changes cannot roll back.
        raise HTTPException(status_code=503, detail="Profile could not be saved. Your sign-in username may already have changed; retry saving") from None

    return user
