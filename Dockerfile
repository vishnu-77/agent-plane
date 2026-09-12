# syntax=docker/dockerfile:1.7
#
# Multi-stage build. The build stage compiles the wheel and installs it, with
# all extras, into a self-contained virtualenv; the runtime stage copies only
# that venv onto a slim base with no compiler, pip, or build metadata.
#
#   docker build -t agent-plane .
#   docker run -p 127.0.0.1:8000:8000 --env-file .env \
#     -e SQLITE_PATH=/data/audit.db -v agent-plane-data:/data agent-plane
#
# Size levers (see docs/deployment.md): no bytecode caches, no bundled tests,
# stripped native extensions, pip/setuptools removed from both the venv and
# the base image. Set EXTRAS to trim further (e.g. "server,postgres").
ARG PYTHON_VERSION=3.12
ARG EXTRAS=all

# ---- build stage --------------------------------------------------------- #
FROM python:${PYTHON_VERSION}-slim AS build
ARG EXTRAS
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1
# binutils provides `strip` for native extensions; it never reaches the runtime image.
RUN apt-get update \
 && apt-get install -y --no-install-recommends binutils \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY pyproject.toml README.md LICENSE MANIFEST.in ./
COPY agent_plane ./agent_plane
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install build \
 && python -m build --wheel --outdir /dist
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-compile "$(ls /dist/*.whl)[${EXTRAS}]" \
 && /opt/venv/bin/pip uninstall -y pip setuptools wheel \
 && find /opt/venv -depth \( -name '__pycache__' -o -name '*.pyc' -o -name '*.pyo' \
        -o -name 'tests' \) -exec rm -rf {} + \
 && find /opt/venv -name '*.so*' -type f -exec strip --strip-unneeded {} + 2>/dev/null || true

# ---- runtime stage --------------------------------------------------------- #
FROM python:${PYTHON_VERSION}-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    SQLITE_PATH=/data/audit.db
# The base image ships pip/setuptools; the venv is self-contained, so drop them.
RUN python -m pip uninstall -y pip setuptools wheel >/dev/null 2>&1 || true \
 && rm -rf /root/.cache /usr/local/lib/python*/ensurepip /usr/local/lib/python*/site-packages/* \
 && find /usr/local/lib/python* -depth -name '__pycache__' -exec rm -rf {} + \
 && useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin appuser \
 && mkdir -p /data /app \
 && chown appuser:appuser /data
COPY --from=build /opt/venv /opt/venv
WORKDIR /app
# Config + policies are read relative to the working dir; mount or bake your own.
COPY --chown=appuser:appuser policies ./policies
COPY --chown=appuser:appuser config ./config

USER appuser
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz').status==200 else 1)"]

CMD ["agentplane", "serve", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
