# syntax=docker/dockerfile:1.7
#
# MirrorMarket API image.
#
# Two stages:
#   builder  - resolves dependencies from uv.lock into a virtualenv (has uv, caches)
#   runtime  - copies only the virtualenv + source; runs as an unprivileged user
#
# Build from the repository root:
#   docker build -t mirrormarket-api:dev backend

ARG PYTHON_IMAGE=python:3.12-slim-trixie

# --------------------------------------------------------------------------- #
FROM ${PYTHON_IMAGE} AS builder

COPY --from=ghcr.io/astral-sh/uv:0.12.21 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app

# Dependencies first: this layer is reused until pyproject.toml/uv.lock change.
COPY pyproject.toml uv.lock .python-version ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

# --------------------------------------------------------------------------- #
FROM ${PYTHON_IMAGE} AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}"

RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY app ./app

# Source is owned by root and the process runs as `app`: the running service
# cannot modify its own code. Normalise permissions so the image works no matter
# what file modes the build context had (e.g. 0600 files from a restrictive umask).
RUN chmod -R a+rX,go-w /app/app

USER app

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=2)"]

CMD ["uvicorn", "app.main:create_app", "--factory", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--no-server-header"]
