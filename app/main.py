import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.agent.router import router as agent_router
from app.api.routes.jobs import router as jobs_router
from app.api.routes.stats import router as stats_router
from app.api.routes.tracks import router as tracks_router
from app.config import settings
from app.core.rate_limit import limiter
from app.spotify.router import router as spotify_router

logging.basicConfig(level=settings.log_level.upper())

app = FastAPI(title="Distributed Job Processing Platform")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(agent_router)
app.include_router(jobs_router)
app.include_router(stats_router)
app.include_router(tracks_router)
app.include_router(spotify_router)
