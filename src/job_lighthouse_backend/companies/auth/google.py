"""POST /auth/google."""

from fastapi import APIRouter

router = APIRouter(prefix="/auth", tags=["auth"])
