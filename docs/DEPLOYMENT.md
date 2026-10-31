# V3 deployment


The V3 repo is `Patr1xk/PropertyPlanetV3`, branch `main`. Separate service names
are configured locally: Cloudflare Worker `propertyplanetv3` (including its
self-reference) and Render Web Service `property-planets-v3-api`. Nothing has
been deployed by preparing these files. Commit and push the intended V3 changes
before connecting either platform; local `.env` files and `.venv` stay private.

Follow this order:

| Step | Action | Result |
| --- | --- | --- |
| 1 | Commit and push V3 to its GitHub repository | Both hosts can build the current code |
| 2 | Create the Render backend | Copy the actual backend HTTPS URL |
| 3 | Create the Cloudflare Worker using that backend URL | Copy the actual frontend HTTPS URL |
| 4 | Set Render's `FRONTEND_URL` to that frontend URL and redeploy Render | Browser requests and Clerk tokens use the correct origin |
| 5 | Verify sign-in and the database-backed flows below | Confirm the hosted app works end to end |

These steps configure new V3 hosting services. There is no database-reset step.
Do not copy your Windows `.venv` to either host: Render installs Python packages
from `backend/requirements.txt`, and Cloudflare installs frontend packages from
`frontend/package-lock.json`. Your local `.env` values are not uploaded; enter
the required values in each platform's settings.

### Render backend first

Create a new Python Web Service from the V3 repo with root directory `backend`.
In the Render dashboard, choose **New > Web Service**, connect
`Patr1xk/PropertyPlanetV3`, select branch `main`, and name it
`property-planets-v3-api`.
Build: `python -m pip install -r requirements.txt`.
Start: `python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1`.

Search endpoints enforce per-account rolling limits: 20 preview requests and 60
paid-search requests per 60 seconds, including pagination and retries. Admins
are exempt. Excess requests return 429 with `Retry-After`, before property queries
or credit deductions. Authentication/profile checks still access Neon. Counters
use fastapi-limiter 0.2.0 with pyrate-limiter's in-memory sliding-window buckets.
They reset on restart and are not shared across processes or instances. Keep
`--workers 1` and one backend instance so account limits remain consistent.
Health check: `/health`. The repo specifies Python 3.12 in `backend/.python-version`.
Alternatively, use the root `render.yaml` as a Blueprint.
Choose either manual Web Service setup or a Blueprint, so you do not create two
backends for the same V3 deployment.

Set these in Render's environment:

| Variable | Value |
| --- | --- |
| `APP_NAME` | `Property Planets V3 API` |
| `DB_URL` | Your existing Neon connection string, kept secret |
| `CLERK_SECRET_KEY` | Secret for the same Clerk instance as the frontend |
| `FRONTEND_URL` | Exact V3 Cloudflare HTTPS origin, no trailing path; replace after Cloudflare supplies the final URL |
| `WHATSAPP_NUMBER` | Your private WhatsApp number (international digits only) |
| `CREDIT_PRICE_MYR` | `1.00` |
| `IMPORT_PROCESSOR_ENABLED` | `true` |

Use the expected V3 frontend origin for the initial deploy, then correct it when
Cloudflare supplies the actual address. The current backend accepts one frontend
origin: this value controls both CORS and Clerk token authorized parties. Do not
put the old V2 frontend origin here. Copy the resulting Render HTTPS service URL.
`/health` confirms API startup only; later verify a signed-in database-backed action.

No additional caching service or environment variables are needed. Each allowed
preview request queries Neon. To run backend tests, install `requirements.txt`
and run `python -m unittest discover -s tests -p "test_*.py"` from `backend/`.

### Property format update

The template/export columns are **Name, Phone 01, Phone 02, IC number, Address,
Remark**. Name maps to `owner_name`; Address is the property address. Name and
Phone 01 are required. Remark is optional (up to 2,000 characters) and appears
only in full records. Free previews return address and record ID only.

For the empty Neon database, run `backend/sql/schema.sql` once, then deploy
the matching backend and frontend. It includes the six-column property format,
credit adjustments, upload expiry and search indexes without seeding data.
Use the new template for Excel/CSV uploads. Do not rerun the schema after data
has been created.

### Search index update for an existing V3 database

Normal search uses six individual GIN indexes; Search Builder shares the
name/address GINs. Every clue requires at least three
characters. Contains/ends-with also require three consecutive letters/digits.
Free previews still search address only.

The selected storage trade-off is six GIN indexes without the two extra prefix
indexes. Deploy the GIN-compatible builder backend first, then run from `backend`:
`python -m scripts.run_sql sql/04_drop_search_prefix_indexes.sql`.
This drops only `properties_owner_name_prefix` and `properties_address_prefix`;
records, all six GIN indexes and duplicate prevention remain. Starts-with/equals
may be slower, as shown in the offline prefix-index comparison. This is an
intentional storage saving. Do not rerun the superseded 02/03 index scripts.
Fresh empty databases use the updated `schema.sql` instead.

### Cloudflare frontend

In Cloudflare's **Workers & Pages** dashboard, create an application and connect
the V3 GitHub repository using the Workers Git build flow.
Create a new **Worker** with Git integration for the V3 repo, not a static Pages
deployment. Use project/Worker name `propertyplanetv3`, branch `main`, and root
directory `frontend`. Build command: `npm run cf:build`. Deploy command:
`npm run cf:deploy`. Use Node.js 22 in the build environment.

Set build variables before building:

| Variable | Value |
| --- | --- |
| `NEXT_PUBLIC_API_URL` | The new Render HTTPS URL, without a trailing slash |
| `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` | Publishable key matching the backend's Clerk instance |
| `CLERK_SECRET_KEY` | Matching secret, stored as a build secret if needed by Clerk during the build |

Also add `CLERK_SECRET_KEY` as a **runtime Worker secret** under the Worker's
Settings > Variables and Secrets. Build secrets and runtime secrets are separate.
Public `NEXT_PUBLIC_*` values are compiled into the frontend: changing them
requires a fresh build. Never add `DB_URL` or a secret under a `NEXT_PUBLIC_*` name.

After deploying, copy the exact Worker HTTPS URL into Render's `FRONTEND_URL`
and redeploy Render. For a hosted test you can reuse the existing Clerk development
instance. For a client production launch, configure a Clerk production instance
and its domain, then update the matching frontend/backend keys together and rebuild.

### Verify the hosted flow

For an empty database, run `backend/sql/schema.sql` once. No separate upgrade
scripts are needed.
Failed or incomplete uploads expire after 24 hours since their last activity,
on the next processor scan (normally within a minute while idle/running).
Queued and processing jobs are excluded. On restart, scanning resumes. Expired
uploads require a new upload; history, row errors and saved properties remain.
Successful-import deletion happens immediately; failed deletions are retried by
maintenance. Keep `IMPORT_PROCESSOR_ENABLED=true` or run the standalone worker
to perform cleanup, for local storage or the configured private bucket.

Large imports keep small Excel string tables in memory (up to a 32 MiB budget)
and spill larger tables to flat temporary files. A streaming text parser avoids
creating XML element objects for every shared string; DTDs/entities are rejected.
Imports then validate and
insert each 5,000-row batch immediately, without a full-file pre-scan or row cache.
The progress bar uses worksheet dimensions as a labeled estimate for Excel, and
animates when a total is unavailable. Completion replaces the estimate with the
actual nonblank row count. No extra counting scan is performed. A later file
error or exceeding 500,000 records retains earlier committed batches. Temporary
indexes are removed when processing exits. Logs report `opening_excel`,
`streaming_rows` and `batch_committed`; the lease is renewed while the reader is opening.
Upload logs also report `stage=upload_chunk` with `receive_seconds` and
`store_seconds`. Receiving starts after authentication in the application;
browser transfer, upstream buffering and authentication can add time before it.
Use browser Network timings alongside these logs to diagnose slow transfers.

If an import makes the API unresponsive, set `IMPORT_PROCESSOR_ENABLED=false` and
redeploy to isolate it. Keep it disabled until the fixed backend is deployed; then
enable it and verify sign-in during a new import. Temporary uploads may need to be
uploaded again after a restart; previously committed records stay in Neon.

Sign in, load the profile, perform a public-preview search, assign a test credit
as Admin, and run a full search. Confirm an empty search does not deduct, paging
does not deduct again, and a refresh resets the paid view. Check the WhatsApp
message and a small import as well. Admin searches remain free.

Neon can remain the existing database by the owner's choice; deploying does not
require rerunning `schema.sql` or deleting data. Separate hosting does not isolate
V2/V3 data while both point at that database. Imports run inside the Render API;
local upload chunks can be lost on restart/redeploy, so use the supported private
bucket setting when durable queued imports are required. Imported records remain
in Neon. The diagrams in `docs/` describe V3's credit and WhatsApp architecture.

### Deployment troubleshooting

Chunk uploads time out after 60 seconds and retry temporary network/server errors
up to twice, with short backoff. Other import requests and upload token retrieval
time out after 30 seconds. Exhausted retries stop the upload with a resume message;
saved chunks remain available. Pausing cancels retries immediately. Job creation
is not automatically retried, and resumed chunks are checked by their saved hash.
Each chunk attempt requests a fresh Clerk token (`skipCache: true`), and a 401
gets one fresh-token retry within the three-attempt limit. Persistent 401s stop;
403 permission failures never retry. Backend rejection logs include the SDK reason
name (for example `TOKEN_EXPIRED`) without logging tokens or account details.

| Symptom | Check |
| --- | --- |
| Frontend calls `localhost:8000` after deployment | Set `NEXT_PUBLIC_API_URL` in Cloudflare's build variables and rebuild |
| API requests fail with CORS or token-origin errors | Render's `FRONTEND_URL` must match the address in the browser, including `https://`, with no path or trailing slash |
| Clerk sign-in loops or reports missing configuration | Match the Clerk instance keys across hosts and set the runtime Worker secret as well as build variables |
| Import uploads but remains Queued | Set `IMPORT_PROCESSOR_ENABLED=true` on Render and redeploy; keep the API at one worker |
| Local Uvicorn traceback points at V2 | Run `.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload` from V3's `backend` directory |

References: [Render FastAPI deployment](https://render.com/docs/deploy-fastapi),
[Cloudflare Next.js deployment](https://developers.cloudflare.com/workers/framework-guides/web-apps/nextjs/),
and [OpenNext build/runtime environment variables](https://opennext.js.org/cloudflare/howtos/env-vars).
