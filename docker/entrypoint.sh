#!/usr/bin/env bash
# Entrypoint dispatcher: maps a single argument to the actual process command.
# Usage examples:
#   entrypoint.sh api                  # gunicorn on :8000
#   entrypoint.sh worker default       # celery worker for queue "default"
#   entrypoint.sh beat                 # celery beat
#   entrypoint.sh migrate              # django migrate
#   entrypoint.sh shell                # django shell
set -euo pipefail

cmd="${1:-api}"
shift || true

case "$cmd" in
    api)
        exec gunicorn notify_relay.wsgi:application \
            --bind 0.0.0.0:8000 \
            --workers "${GUNICORN_WORKERS:-4}" \
            --timeout "${GUNICORN_TIMEOUT:-30}" \
            --access-logfile - \
            --error-logfile -
        ;;
    worker)
        queue="${1:-default}"
        concurrency="${CELERY_CONCURRENCY:-4}"
        exec celery -A notify_relay worker \
            -Q "$queue" \
            -n "${queue}@%h" \
            --concurrency "$concurrency" \
            --loglevel "${CELERY_LOG_LEVEL:-INFO}"
        ;;
    beat)
        exec celery -A notify_relay beat \
            --loglevel "${CELERY_LOG_LEVEL:-INFO}" \
            --scheduler "${CELERY_BEAT_SCHEDULER:-celery.beat:PersistentScheduler}"
        ;;
    migrate)
        exec python manage.py migrate --noinput
        ;;
    shell)
        exec python manage.py shell
        ;;
    bash|sh)
        exec /bin/bash
        ;;
    *)
        exec "$cmd" "$@"
        ;;
esac
