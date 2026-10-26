from fastapi import APIRouter

from app.routers.auth_router import router as auth_router
from app.routers.tokens_router import router as tokens_router
from app.routers.patients_router import router as patients_router

# Unified Geosmin 2.0 Router for clean, single-line mounting in main applications:
# e.g.: app.include_router(geosmin_v2_router)
geosmin_v2_router = APIRouter()
geosmin_v2_router.include_router(auth_router)
geosmin_v2_router.include_router(tokens_router)
geosmin_v2_router.include_router(patients_router)

# Gracefully import speech_router if present from parallel teammate work
try:
    from .speech_router import speech_router, router as speech_sub_router
    geosmin_v2_router.include_router(speech_router)
except ImportError:
    speech_router = None

__all__ = [
    "auth_router",
    "tokens_router",
    "patients_router",
    "geosmin_v2_router",
    "speech_router",
]
