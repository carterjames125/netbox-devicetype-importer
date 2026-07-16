FROM python:3.12-slim

# git is required at runtime by GitPython to clone/pull the Device Type Library.
# safe.directory is set at the system level (not --global) so it applies no
# matter which non-root UID the container ends up running as.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && git config --system --add safe.directory /app/repo

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY pyproject.toml uv.lock README.md ./
COPY devicetype_importer ./devicetype_importer

RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:${PATH}"

# Run as a non-root user so bind-mounted repo/logs directories aren't written
# as root on the host. Defaults to UID/GID 1000 (the common first-user id on
# most Linux distros); override at build time to match your host user, e.g.:
#   docker build --build-arg APP_UID=$(id -u) --build-arg APP_GID=$(id -g) .
ARG APP_UID=1000
ARG APP_GID=1000
RUN groupadd -g "${APP_GID}" app \
    && useradd -m -u "${APP_UID}" -g "${APP_GID}" app \
    && mkdir -p /app/repo /app/logs \
    && chown -R app:app /app

USER app

ENTRYPOINT ["nb-dt-import"]
