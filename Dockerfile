# One image for both services and migrations. Compose picks the command.
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /bin/uv

# PROJ-003: Trivy fails the build on fixable HIGH/CRITICAL findings.
# - apt upgrade: pick up Debian security fixes newer than the base image.
# - drop system pip: the app runs from uv's venv, and pip's vendored
#   packages (msgpack, setuptools) showed up as findings.
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip uninstall -y pip

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

# Dependencies first, so code changes don't reinstall them.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project

# PROJ-009: Chromium for `dynamic` scrapes and the detect browser fallback.
# Headless shell only (launch() is headless). Installed under a shared path,
# not root's home, so the non-root `app` user can read it.
RUN playwright install --with-deps --only-shell chromium \
    && rm -rf /var/lib/apt/lists/* \
    && chmod -R a+rX /ms-playwright

COPY src ./src
COPY alembic.ini ./
COPY migrations ./migrations
RUN uv sync --locked --no-dev

ARG GIT_SHA=unknown
LABEL org.opencontainers.image.revision=$GIT_SHA

RUN useradd --system --no-create-home app
USER app

EXPOSE 8000
