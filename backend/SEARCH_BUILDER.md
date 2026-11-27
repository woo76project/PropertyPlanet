# V3 search builder

Property browsing and Admin property management support up to ten conditions total,
with at most five per category, on
name and address. Choose **all** (AND) or **any** (OR).
Conditions support contains, starts with, ends with and equals; matching ignores
letter case. This is literal fragment matching, not typo correction or semantic AI
search. Add two contains conditions on the same field to remember separate words
in any order. Example: address contains `Damansara` AND name starts with `Tan`.

The builder replaces quick search when applied. Page size remains
active; changes reset the pagination cursor. Quick search replaces the active
builder. Free users can filter names and addresses in the builder, including AND/OR
combinations. Preview responses still expose only names and addresses.
Free quick search remains address-only. Full searches use the credit-protected
POST /properties/search endpoint. Admin retains direct full-record access.
The current rule is one credit for a successful search with matching rows; empty results cost zero and
all pages of the same query are included. No 50/100-record cap has been applied.

## Why V1 can be slow

The old `worker/properties.ts` uses `ILIKE '%fragment%'`, a full `COUNT(*)` for each
search and OFFSET pagination. Ordinary B-tree indexes in
`POSTGRES_PROPERTIES_SCHEMA.sql` do not solve the leading-wildcard searches; its
recommended GIN trigram indexes are commented out. This is source-level evidence,
not proof of what indexes are installed on the old hosted database.

V3 keeps cursor pagination (`id < before_id`) with `LIMIT + 1`, no full count.
`sql/schema.sql` includes six individual GIN trigram indexes. Builder equality
and prefix queries use a plain-column ILIKE candidate plus the original LOWER
comparison as a recheck. There are no additional text_pattern_ops prefix indexes.
This trades some equality/prefix speed for lower storage usage.
Normal search uses per-field OR conditions with six individual GIN indexes.
Search Builder and public address previews share the name/address indexes.
There is no combined-text expression index or extra stored search column.
Indexes consume disk and slow inserts/imports; measure that tradeoff with V3 data.

All operators and normal search accept at least one trimmed character. `%` and
`_` remain literal text, escaped by the API. One- and two-character contains
queries may provide no useful trigrams and can scan many rows; pagination and
rate limits remain enabled. The builder allows five names and five addresses.

## Database setup

All search indexes are included in the single complete V3 schema:

```powershell
python -m scripts.run_sql sql/schema.sql
```

Run it once against an empty application database, or paste the full schema into
Neon's SQL editor after manually removing the old tables. There is no separate
search-index migration for a fresh database. Existing V3 databases instead run
`python -m scripts.run_sql sql/04_drop_search_prefix_indexes.sql` after deploying
the matching GIN-compatible builder backend. It removes only the two extra
prefix indexes; records, six GIN indexes and duplicate prevention are preserved.
Do not rerun the superseded 02/03 scripts. The one-character validation remains.
Do not rerun the complete schema on an existing database. The schema enables `pg_trgm` and `pgcrypto`, creates the
final tables with their constraints and indexes, and refreshes search statistics.
It does not reset existing data or create any accounts.

## Read-only inspection — 21 October 2026

The database inspected on 21 October 2026 exposed only
`properties_pkey` (B-tree on id) and `properties_duplicate_key_unique` (B-tree on
duplicate_key). There were no text-search indexes on that table at inspection time. EXPLAIN without
ANALYZE selected a sequential scan for the two-field contains query, and primary
key scans for the single-field contains and short owner-prefix examples. These
are estimated plans, not measured execution times or a before/after benchmark.
This inspection does not establish the schema of V1's separate database.

Recheck without displaying records or secrets, from `backend`:

```powershell
python -m scripts.inspect_property_search
```

Initial search-builder verification (before the credit migration): 150 backend
tests, 70 frontend API tests, targeted ESLint, TypeScript and the production build
passed. The subsequent credit-based application passed 132 backend tests, 64
frontend API tests, three isolated PostgreSQL tests, lint, TypeScript and the
production build. PostgreSQL tests use session-local temporary tables. No
application migration, deployment or live search-performance benchmark was run.

## Verify performance

Inspect `pg_indexes` and use EXPLAIN (ANALYZE, BUFFERS) on representative V3 queries,
including rare/common clues, all/any combinations, and subsequent cursor pages:

```sql
SELECT indexname, indexdef FROM pg_indexes
WHERE schemaname = 'public' AND tablename = 'properties';

EXPLAIN (ANALYZE, BUFFERS)
SELECT id FROM properties
WHERE (address ILIKE '%Damansara%' ESCAPE '!'
       AND address ILIKE '%Jalan%' ESCAPE '!')
ORDER BY id DESC LIMIT 21;

EXPLAIN (ANALYZE, BUFFERS)
SELECT id FROM properties
WHERE LOWER(owner_name) LIKE LOWER('Tan%') ESCAPE '!'
ORDER BY id DESC LIMIT 21;
```

Index availability alone does not guarantee index use; PostgreSQL chooses based
on selectivity/statistics, table size and ordering cost. Do not force index usage
for a production performance claim. Distinguish SQL execution time from Render
or Neon cold starts and frontend authentication/membership round trips.

Reference: https://www.postgresql.org/docs/current/pgtrgm.html
