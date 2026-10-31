import logging
import asyncio
from contextlib import asynccontextmanager

import psycopg
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from app.core.config import get_settings
from app.auth import router as auth_router
from app.account import router as account_router
from app.properties import router as properties_router
from app.credits import router as credits_router
from app.admin import router as admin_router
from app.property_files import router as property_files_router
from app.audit import router as audit_router
from app.import_jobs import router as import_jobs_router

settings = get_settings()


@asynccontextmanager
async def lifespan(app):
    processor = None
    if settings.import_processor_enabled:
        from app.import_processor import ImportProcessor
        processor = ImportProcessor()
        app.state.import_processor = processor
        processor.start()
    try:
        yield
    finally:
        if processor is not None:
            await asyncio.to_thread(processor.stop)
            app.state.import_processor = None


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url],
    # Requests use Clerk Bearer tokens; no cross-origin cookie credentials are needed.
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
    expose_headers=["Content-Disposition", "Retry-After"],
)


@app.exception_handler(psycopg.Error)
async def database_error_handler(request: Request, error: psycopg.Error):
    # Never put raw connection errors, SQL parameters, or credentials in the response/log.
    logging.getLogger(__name__).error("Database operation failed (%s)", type(error).__name__)
    return JSONResponse(status_code=503, content={"detail": "Database is temporarily unavailable"})


app.include_router(auth_router)
app.include_router(account_router)
app.include_router(import_jobs_router)
# Named file routes must come before /properties/{property_id}.
app.include_router(property_files_router)
app.include_router(properties_router)
app.include_router(credits_router)
app.include_router(admin_router)
app.include_router(audit_router)

@app.get("/health")
def health_check():
    return {"status": "healthy"}

