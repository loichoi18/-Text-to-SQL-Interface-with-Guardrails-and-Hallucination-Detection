.PHONY: up down build logs test lint seed evals fmt help

help:
	@echo "Common targets:"
	@echo "  make up       Start db + api + frontend (docker-compose)"
	@echo "  make down     Stop containers"
	@echo "  make build    Rebuild images"
	@echo "  make logs     Tail container logs"
	@echo "  make test     Run unit tests (no DB required)"
	@echo "  make seed     Regenerate seed/02_seed_data.sql"
	@echo "  make evals    Run the golden eval suite against the API"

up:
	docker compose up -d --build

down:
	docker compose down

build:
	docker compose build

logs:
	docker compose logs -f --tail=100

test:
	PYTHONPATH=. pytest tests/ -q

seed:
	python seed/generate_seed.py

evals:
	python evals/run_evals.py
