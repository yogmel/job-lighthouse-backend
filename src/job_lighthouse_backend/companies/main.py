"""Companies Service: /companies, /auth, /account and onboarding detection."""

from job_lighthouse_backend.common.app import create_app

from . import account
from .auth import google, login, signup

app = create_app("Companies Service")
app.include_router(signup.router)
app.include_router(login.router)
app.include_router(google.router)
app.include_router(account.router)
