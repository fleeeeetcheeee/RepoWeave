FROM ghcr.io/astral-sh/uv:0.13.1 AS uv
FROM python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258

RUN apt-get update && apt-get install -y --no-install-recommends tini gosu \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 repoweave
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH"
COPY pyproject.toml uv.lock /app/
COPY src /app/src
RUN uv sync --locked --no-dev --no-editable
COPY examples/demo_repository /app/examples/demo_repository
COPY deploy/start-hosting.sh /app/deploy/start-hosting.sh
ENTRYPOINT ["/usr/bin/tini", "--", "/bin/sh", "/app/deploy/start-hosting.sh"]
