# AEGIS AI + SENTINEL. Run `make help` for targets. Tested with GNU make 3.81 (macOS default) and 4.x.
PY ?= python3
VENV := .venv
BIN := $(VENV)/bin

.PHONY: help setup init dev dev-backend dev-frontend test test-backend test-frontend build serve reset-demo e2e clean link-ecommerce shop-setup shop-init shop-dev shop-test

help:
	@echo "setup         create .venv, install Python and npm dependencies"
	@echo "init          create the database, seed built-ins, create the first admin"
	@echo "dev-backend   start the API on http://127.0.0.1:8000 (auto-reload)"
	@echo "dev-frontend  start the UI on http://127.0.0.1:5173"
	@echo "dev           start both (Ctrl+C stops both)"
	@echo "test          run backend (pytest) and frontend (vitest) tests"
	@echo "build         build the UI into frontend/dist (served by the API with 'make serve')"
	@echo "serve         run the API and the built UI together on http://127.0.0.1:8000"
	@echo "reset-demo    DELETE data/ and re-initialise (fresh empty install)"
	@echo "shop-setup    install the e-commerce platform's dependencies"
	@echo "shop-init     create and seed the e-commerce database"
	@echo "link-ecommerce  create the SENTINEL service token and internal token, write both .env files"
	@echo "shop-dev      start the shop API (8100) and storefront/Workbench (5174)"
	@echo "shop-test     run the e-commerce end-to-end tests"

setup:
	$(PY) -c 'import sys; assert sys.version_info >= (3, 10), "Python 3.10+ required"'
	$(PY) -m venv $(VENV)
	$(BIN)/pip install --upgrade pip
	$(BIN)/pip install -r backend/requirements.txt
	cd frontend && npm ci
	@test -f .env || cp .env.example .env

init:
	cd backend && ../$(BIN)/python -m app.cli init

dev-backend:
	cd backend && ../$(BIN)/uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

dev-frontend:
	cd frontend && npm run dev -- --host 127.0.0.1

dev:
	@trap 'kill 0' INT TERM; $(MAKE) dev-backend & $(MAKE) dev-frontend & wait

test: test-backend test-frontend

test-backend:
	$(BIN)/python -m pytest -q

test-frontend:
	cd frontend && npx tsc -p tsconfig.json --noEmit && npm test

build:
	cd frontend && npm run build

serve: build
	cd backend && ../$(BIN)/uvicorn app.main:app --host 127.0.0.1 --port 8000

reset-demo:
	rm -rf data && mkdir data && touch data/.gitkeep
	$(MAKE) init

e2e:
	node scripts/e2e_browser.js

clean:
	rm -rf $(VENV) frontend/node_modules frontend/dist .pytest_cache

shop-setup:
	$(MAKE) -C ecommerce-agent-platform setup

shop-init:
	$(MAKE) -C ecommerce-agent-platform init

link-ecommerce:
	$(BIN)/python scripts/link_ecommerce.py

shop-dev:
	$(MAKE) -C ecommerce-agent-platform dev

shop-test:
	$(MAKE) -C ecommerce-agent-platform test
