"""Read-only index/query-plan inspection; never executes the searched SELECTs."""
import json

import psycopg
from psycopg.rows import dict_row

from app.core.config import get_settings


def summarize(plan):
    result = {key: plan[key] for key in (
        "Node Type", "Index Name", "Plan Rows", "Total Cost"
    ) if key in plan}
    if "Plans" in plan:
        result["Plans"] = [summarize(child) for child in plan["Plans"]]
    return result


def main():
    try:
        with psycopg.connect(get_settings().db_url.get_secret_value(), row_factory=dict_row,
                             connect_timeout=15) as connection:
            connection.execute("SET TRANSACTION READ ONLY")
            connection.execute("SET LOCAL statement_timeout = '10s'")
            indexes = connection.execute(
                "SELECT indexname, indexdef FROM pg_indexes "
                "WHERE schemaname = current_schema() AND tablename = 'properties' ORDER BY indexname"
            ).fetchall()
            print(json.dumps({"indexes": indexes}, indent=2))
            for label, condition, values in (
                ("contains", "address ILIKE %s ESCAPE '!'", ("%Damansara%",)),
                ("builder_all", "address ILIKE %s ESCAPE '!' AND address ILIKE %s ESCAPE '!'",
                 ("%Damansara%", "%Jalan%")),
                ("short_owner_prefix", "LOWER(owner_name) LIKE LOWER(%s) ESCAPE '!'", ("Ng%",)),
            ):
                row = connection.execute(
                    "EXPLAIN (FORMAT JSON) SELECT id FROM properties WHERE " + condition
                    + " ORDER BY id DESC LIMIT 21", values,
                ).fetchone()
                plan = next(iter(row.values()))[0]["Plan"]
                print(json.dumps({label: summarize(plan)}, indent=2))
    except Exception as error:
        # Connection/provider errors may contain secrets. Print only their type.
        print(f"Search inspection unavailable ({type(error).__name__})")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
