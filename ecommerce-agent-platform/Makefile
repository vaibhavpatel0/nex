# Halden Bazaar e-commerce agent platform. Uses the repository's .venv (create it with `make setup` at the root).
VENV := ../.venv
BIN := $(VENV)/bin

.PHONY: help setup init dev dev-backend dev-frontend test test-backend test-frontend build serve check-model reset

help:
	@echo "setup         install shop Python deps into ../.venv and npm deps"
	@echo "init          create data/shop.db, apply migrations, seed synthetic data"
	@echo "dev-backend   API on http://127.0.0.1:8100 (auto-reload)"
	@echo "dev-frontend  storefront and Agent Workbench on http://127.0.0.1:5174"
	@echo "dev           both (Ctrl+C stops both)"
	@echo "check-model   send ONE tool-calling request to the configured model (cloud models: counts against your plan)"
	@echo "test          end-to-end tests (real AEGIS + shop + an Ollama-protocol test double) and UI tests"
	@echo "reset         DELETE data/ and re-initialise"

setup:
	$(BIN)/pip install -r backend/requirements.txt
	cd frontend && npm ci
	@test -f .env || (cp .env.example .env && chmod 600 .env)

init:
	cd backend && ../$(BIN)/python -m shop.cli init

dev-backend:
	cd backend && ../$(BIN)/uvicorn shop.main:app --host 127.0.0.1 --port 8100 --reload

dev-frontend:
	cd frontend && npm run dev -- --host 127.0.0.1

dev:
	@trap 'kill 0' INT TERM; $(MAKE) dev-backend & $(MAKE) dev-frontend & wait

check-model:
	cd backend && ../$(BIN)/python -m shop.cli check-model

test: test-backend test-frontend

test-backend:
	$(BIN)/python -m pytest -q

test-frontend:
	cd frontend && npx tsc -b --noEmit && npm test

build:
	cd frontend && npm run build

serve: build
	cd backend && ../$(BIN)/uvicorn shop.main:app --host 127.0.0.1 --port 8100

reset:
	rm -rf data && mkdir data && touch data/.gitkeep
	$(MAKE) init
