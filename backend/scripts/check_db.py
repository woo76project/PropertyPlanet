import psycopg

from app.core.config import get_settings

def main():
    settings = get_settings()

    with psycopg.connect(settings.db_url.get_secret_value()) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            result = cur.fetchone()

    if result != (1,):
        raise RuntimeError("Database connection failed.")

    print("Database (Neon) connection successful.")


if __name__ == "__main__":
    main()