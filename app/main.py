import logging

from fastapi import FastAPI

from app.api.routes.jobs import router as jobs_router
from app.config import settings

logging.basicConfig(level=settings.log_level.upper())

app = FastAPI(title="Distributed Job Processing Platform")
app.include_router(jobs_router)
