# ---- build stage: produce a wheel ----
FROM python:3.12-slim AS build
WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY agent_plane ./agent_plane
COPY policies ./policies
COPY config ./config
RUN pip install --no-cache-dir build && python -m build --wheel

# ---- runtime stage: slim, non-root ----
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
RUN useradd -m -u 10001 appuser
WORKDIR /app

# Install the wheel with the batteries-included extras so all edges work.
COPY --from=build /src/dist/*.whl /tmp/
RUN pip install --no-cache-dir "$(ls /tmp/*.whl)[all]" && rm -rf /tmp/*.whl

# Config + policies are read relative to the working dir.
COPY policies ./policies
COPY config ./config

USER appuser
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz').status==200 else 1)"

CMD ["agentplane", "serve", "--host", "0.0.0.0", "--port", "8000"]
