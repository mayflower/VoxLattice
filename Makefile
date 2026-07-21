SHELL := /bin/bash
.DEFAULT_GOAL := help

UV := uv
COMPOSE := docker compose -f deploy/docker-compose.yml
MODEL_ARCHIVE := models/cache/fastenhancer_b.zip
A6000_UUID := GPU-bac67bca-195d-3490-88f0-b8a3453c5929
PIP_AUDIT_IGNORES := --ignore-vuln PYSEC-2026-139 --ignore-vuln PYSEC-2025-194

.PHONY: help bootstrap check format proto proto-check test test-integration audit model image up smoke compose-test test-gpu benchmark down clean

help:
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z_-]+:.*## / {printf "%-20s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

bootstrap: ## Install the frozen workspace and verify checked-in protobuf sources
	$(UV) sync --all-packages --dev --frozen
	$(MAKE) proto-check
	$(UV) run python tools/vendor_fastenhancer.py

check: ## Run formatting, lint, typing, and proto drift checks
	$(UV) run ruff format --check .
	$(UV) run ruff check .
	$(UV) run mypy
	$(MAKE) proto-check

format: ## Apply Ruff formatting and safe lint fixes
	$(UV) run ruff format .
	$(UV) run ruff check . --fix

proto: ## Regenerate checked-in protobuf Python sources
	$(UV) run python tools/generate_proto.py

proto-check: ## Fail if generated protobuf sources drift from the schema
	$(UV) run python tools/check_proto.py

test: ## Run CPU/unit tests (production never selects a CPU model)
	$(UV) run pytest -m "not integration and not gpu"

test-integration: ## Run in-process gRPC and LiveKit transport tests
	$(UV) run pytest -m integration

audit: ## Audit dependencies with the reviewed PyTorch exceptions in docs/security.md
	$(UV) run pip-audit --local --progress-spinner off $(PIP_AUDIT_IGNORES)

model: ## Fetch, hash-check, and atomically prepare the official model
	$(UV) run python tools/fetch_fastenhancer.py
	$(UV) run python tools/verify_model.py

image: model ## Build the pinned server image
	docker build --pull=false -f services/fastenhancer-server/Dockerfile -t voxlattice:0.1.0 .

up: ## Start the local A6000-only Compose deployment
	$(COMPOSE) up -d --wait --wait-timeout 120

smoke: ## Stream deterministic PCM through the running server
	$(UV) run python examples/grpc-client/client.py --endpoint 127.0.0.1:50051 --duration-s 1

compose-test: model ## Build, start, smoke-test, and stop the local A6000 Compose stack
	@set -eu; \
		if [ -f deploy/.env ]; then set -a; . deploy/.env; set +a; fi; \
		test -n "$${FASTENHANCER_API_TOKEN:-}" || (echo "FASTENHANCER_API_TOKEN or deploy/.env is required" >&2; exit 2); \
		trap '$(COMPOSE) --profile test down --remove-orphans' EXIT; \
		FASTENHANCER_GRPC_PORT=0 FASTENHANCER_HTTP_PORT=0 \
			$(COMPOSE) --profile test up --build --abort-on-container-exit --exit-code-from smoke smoke

test-gpu: model ## Run the real-model RTX A6000 test suite
	CUDA_VISIBLE_DEVICES=$(A6000_UUID) $(UV) run pytest -m gpu -vv

benchmark: ## Run the real-time load generator and write JSON/Markdown artifacts
	$(UV) run python benchmarks/load.py

down: ## Stop the Compose deployment
	$(COMPOSE) down --remove-orphans

clean: ## Remove only generated build/test caches (model cache is preserved)
	$(UV) run python tools/clean.py
