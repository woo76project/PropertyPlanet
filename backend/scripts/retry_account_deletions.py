"""Finish interrupted deletions without needing a user's login token.

Run from backend: python -m scripts.retry_account_deletions
Only processes accounts that already requested deletion (status Deleting).
"""
import psycopg
from fastapi import HTTPException
from psycopg.rows import dict_row

from app.account import delete_user_account
from app.core.config import get_settings


def main():
    settings = get_settings()
    with psycopg.connect(
        settings.db_url.get_secret_value(), row_factory=dict_row, connect_timeout=15,
    ) as connection:
        # Stream pending records rather than loading all accounts into memory.
        with connection.cursor(name="pending_account_deletions") as cursor:
            cursor.execute("SELECT clerk_user_id FROM users WHERE status = 'Deleting'")
            for row in cursor:
                try:
                    delete_user_account(row["clerk_user_id"], settings)
                    print("Account deletion completed.")
                except HTTPException:
                    # Keep private identifiers and provider errors out of terminal output.
                    print("Account deletion still pending; retry later.")


if __name__ == "__main__":
    main()
