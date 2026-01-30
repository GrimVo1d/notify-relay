.PHONY: help up down restart logs ps migrate makemigrations shell test test-unit test-integration lint fmt clean

COMPOSE := docker compose

help:
	@echo "Targets:"
	@echo "  up                docker compose up -d"
	@echo "  down              docker compose down"
	@echo "  restart           docker compose restart api worker-default"
	@echo "  logs              tail logs from all services"
	@echo "  ps                list running services"
	@echo "  migrate           run django migrations"
	@echo "  makemigrations    create new migrations"
	@echo "  shell             open django shell"
	@echo "  test              run full pytest suite"
	@echo "  test-unit         run unit tests only"
	@echo "  test-integration  run integration tests only"
	@echo "  lint              ruff + black --check + mypy"
	@echo "  fmt               black + isort + ruff --fix"
	@echo "  clean             remove caches and pyc"

up:
	$(COMPOSE) up -d

down:
	$(COMPOSE) down

restart:
	$(COMPOSE) restart api worker-default

logs:
	$(COMPOSE) logs -f --tail=200

ps:
	$(COMPOSE) ps

migrate:
	$(COMPOSE) exec api python manage.py migrate

makemigrations:
	$(COMPOSE) exec api python manage.py makemigrations

shell:
	$(COMPOSE) exec api python manage.py shell

test:
	pytest -q

test-unit:
	pytest -q tests/unit

test-integration:
	pytest -q tests/integration

lint:
	ruff check src tests
	black --check src tests
	mypy src

fmt:
	isort src tests
	black src tests
	ruff check --fix src tests

clean:
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf .mypy_cache .ruff_cache .pytest_cache .coverage htmlcov
