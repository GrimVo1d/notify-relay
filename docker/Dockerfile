# Pinned by digest so a rebuild gets the same base; bump tag and digest together.
ARG PYTHON_IMAGE=python:3.12.15-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1

FROM ${PYTHON_IMAGE} AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# Runtime dependencies only, pinned and hash-checked. The project code is
# not installed: it is copied as-is into the runtime stage, so this layer
# stays cached across code changes.
COPY requirements.lock /tmp/requirements.lock
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --require-hashes --no-deps -r /tmp/requirements.lock


FROM ${PYTHON_IMAGE} AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/app/src \
    DJANGO_SETTINGS_MODULE=notify_relay.settings.prod

RUN apt-get update \
 && apt-get install -y --no-install-recommends tini \
 && rm -rf /var/lib/apt/lists/*

RUN groupadd --system --gid 1000 app \
 && useradd --system --uid 1000 --gid app --no-create-home --shell /usr/sbin/nologin app

COPY --from=builder /opt/venv /opt/venv

# Code stays owned by root: the app user can read it but not modify it.
WORKDIR /app
COPY manage.py ./
COPY src ./src
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh

USER app

EXPOSE 8000

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
CMD ["api"]
