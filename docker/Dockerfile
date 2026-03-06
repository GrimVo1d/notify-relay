ARG PYTHON_VERSION=3.12

FROM python:${PYTHON_VERSION}-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential libpq-dev curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY pyproject.toml README.md ./
# Wheel only the runtime dependencies — the project's own wheel is built
# fresh in the runtime stage where src/ is available. This keeps the
# heavy dependency layer cacheable across code changes.
RUN python -m pip install --upgrade pip wheel \
 && python -c "import tomllib; \
deps = tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']; \
open('/tmp/runtime-requirements.txt','w').write('\n'.join(deps))" \
 && python -m pip wheel --wheel-dir /wheels -r /tmp/runtime-requirements.txt


FROM python:${PYTHON_VERSION}-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    DJANGO_SETTINGS_MODULE=notify_relay.settings.dev

RUN apt-get update \
 && apt-get install -y --no-install-recommends libpq5 curl tini \
 && rm -rf /var/lib/apt/lists/*

RUN groupadd --system --gid 1000 app \
 && useradd  --system --uid 1000 --gid app --home /app --shell /bin/bash app

WORKDIR /app

COPY --from=builder /wheels /wheels
RUN python -m pip install --no-cache-dir /wheels/*.whl \
 && rm -rf /wheels

COPY --chown=app:app pyproject.toml README.md ./
COPY --chown=app:app src ./src
COPY --chown=app:app manage.py ./manage.py
RUN python -m pip install --no-cache-dir --no-deps -e .
COPY --chown=app:app docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

ENV PYTHONPATH=/app/src

USER app

EXPOSE 8000

ENTRYPOINT ["/usr/sbin/tini", "--", "/usr/local/bin/entrypoint.sh"]
CMD ["api"]
