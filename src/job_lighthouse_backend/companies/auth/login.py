"""POST /auth/login."""

from fastapi import APIRouter

router = APIRouter(prefix="/auth", tags=["auth"])
