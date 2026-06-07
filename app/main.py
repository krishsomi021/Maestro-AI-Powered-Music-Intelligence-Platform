import logging

from fastapi import FastAPI
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.api.routes.jobs import router as jobs_router
from app.config import settings
from app.core.rate_limit import limiter

logging.basicConfig(level=settings.log_level.upper())

app = FastAPI(title="Distributed Job Processing Platform")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.include_router(jobs_router)
