from pathlib import Path
import sys

import psycopg

from app.core.config import get_settings

settings = get_settings()

sql_file = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).resolve().parents[1] / "sql" / "schema.sql")
sql = Path(sql_file).read_text(encoding="utf-8")

with psycopg.connect(
    settings.db_url.get_secret_value()
) as connection:
    connection.execute(sql)

print("SQL executed successfully")
