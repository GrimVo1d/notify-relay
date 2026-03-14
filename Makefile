.PHONY: help dev-setup test lint fmt typecheck audit lock up down logs ps migrate makemigrations migrations-check superuser shell dbshell clean

COMPOSE := docker compose

# Tooling runs in a throwaway python:3.12 container with the repo mounted and
# the virtualenv kept in a named volume, so nothing is installed on the host.
PY_IMAGE := python:3.12-slim
DEV_RUN := docker run --rm -t -v "$(CURDIR)":/app -v notify-relay-venv:/venv -w /app \
	-e VIRTUAL_ENV=/venv -e PATH=/venv/bin:/usr/local/bin:/usr/bin:/bin \
	-e DJANGO_SETTINGS_MODULE=notify_relay.settings.test $(PY_IMAGE)

help:
	@echo "Development (in a container):"
	@echo "  dev-setup          create the dev virtualenv volume from requirements-dev.lock"
	@echo "  test               pytest"
	@echo "  lint               ruff check + ruff format --check"
	@echo "  fmt                ruff format + ruff check --fix"
	@echo "  typecheck          mypy"
	@echo "  audit              pip-audit against requirements.lock"
	@echo "  lock               re-pin requirements.lock and requirements-dev.lock"
	@echo "Stack (docker compose):"
	@echo "  up / down / logs / ps"
	@echo "  migrate            apply migrations"
	@echo "  makemigrations     create migrations"
	@echo "  migrations-check   fail if models and migrations differ"
	@echo "  superuser          create a staff user"
	@echo "  shell / dbshell    django shell / psql"
	@echo "  clean              remove caches"

dev-setup:
	$(DEV_RUN) bash -c "python -m venv /venv && pip install -q --require-hashes --no-deps -r requirements-dev.lock"

test:
	$(DEV_RUN) pytest -q

lint:
	$(DEV_RUN) bash -c "ruff check src tests && ruff format --check src tests"

fmt:
	$(DEV_RUN) bash -c "ruff format src tests && ruff check --fix src tests"

typecheck:
	$(DEV_RUN) mypy src

audit:
	$(DEV_RUN) pip-audit --strict --disable-pip --require-hashes -r requirements.lock

lock:
	docker run --rm -v "$(CURDIR)":/repo -w /work $(PY_IMAGE) bash -c "\
		cp /repo/pyproject.toml /repo/README.md . && mkdir src && \
		pip install -q pip-tools && \
		export CUSTOM_COMPILE_COMMAND='make lock' && \
		pip-compile -q --strip-extras --generate-hashes --allow-unsafe -o requirements.lock pyproject.toml && \
		pip-compile -q --strip-extras --generate-hashes --allow-unsafe --extra dev -o requirements-dev.lock pyproject.toml && \
		cp requirements.lock requirements-dev.lock /repo/"

up:
	$(COMPOSE) up -d --build

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f --tail=200

ps:
	$(COMPOSE) ps

migrate:
	$(COMPOSE) exec api python manage.py migrate

makemigrations:
	$(DEV_RUN) python manage.py makemigrations

migrations-check:
	$(DEV_RUN) python manage.py makemigrations --check --dry-run

superuser:
	$(COMPOSE) exec api python manage.py createsuperuser

shell:
	$(COMPOSE) exec api python manage.py shell

dbshell:
	$(COMPOSE) exec db psql -U nr -d notify_relay

clean:
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf .mypy_cache .ruff_cache .pytest_cache .hypothesis .coverage htmlcov
