SHELL := /bin/bash
.DEFAULT_GOAL := help

UV := uv
PYTHON := python3
COMPOSE := docker compose -f deploy/docker-compose.yml
PIP_AUDIT_IGNORES := --ignore-vuln PYSEC-2026-139 --ignore-vuln PYSEC-2025-194

.PHONY: help bootstrap check workflow-check vendor-check format proto proto-check test test-integration audit model wheels image up smoke enhance compose-test test-gpu benchmark down clean

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
	$(UV) run python tools/check_docs.py
	$(MAKE) proto-check
	$(MAKE) vendor-check
	$(MAKE) workflow-check

workflow-check: ## Audit GitHub Actions workflows for unsafe patterns
	$(UV) run zizmor --pedantic .github/workflows

vendor-check: ## Verify vendored FastEnhancer files against locked provenance
	$(UV) run python tools/vendor_fastenhancer.py

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
	$(PYTHON) tools/fetch_fastenhancer.py
	$(PYTHON) tools/verify_model.py

wheels: ## Build all distributable Python wheels
	$(UV) run python -m build generated
	$(UV) run python -m build third_party/fastenhancer
	$(UV) run python -m build packages/livekit-plugins-fastenhancer
	$(UV) run python -m build services/fastenhancer-server

image: model ## Build the pinned server image
	docker build --pull=false -f services/fastenhancer-server/Dockerfile -t voxlattice:0.1.0 .

up: model ## Build and start the local GPU Compose deployment
	@set -eu; \
		if [ -f deploy/.env ]; then set -a; . deploy/.env; set +a; fi; \
		test -n "$${FASTENHANCER_API_TOKEN:-}" || (echo "FASTENHANCER_API_TOKEN or deploy/.env is required" >&2; exit 2); \
		test -n "$${FASTENHANCER_GPU_DEVICE_ID:-}" || (echo "FASTENHANCER_GPU_DEVICE_ID or deploy/.env is required" >&2; exit 2); \
		$(COMPOSE) up -d --build --wait --wait-timeout 120

smoke: ## Stream deterministic PCM through the running server
	@set -eu; \
		if [ -f deploy/.env ]; then set -a; . deploy/.env; set +a; fi; \
		test -n "$${FASTENHANCER_API_TOKEN:-}" || (echo "FASTENHANCER_API_TOKEN or deploy/.env is required" >&2; exit 2); \
		$(COMPOSE) --profile test run --rm smoke

enhance: ## Enhance INPUT WAV and write OUTPUT WAV through the running server
	@set -eu; \
		if [ -f deploy/.env ]; then set -a; . deploy/.env; set +a; fi; \
		test -n "$${FASTENHANCER_API_TOKEN:-}" || (echo "FASTENHANCER_API_TOKEN or deploy/.env is required" >&2; exit 2); \
		test -n "$(INPUT)" || (echo "usage: make enhance INPUT=input.wav OUTPUT=enhanced.wav" >&2; exit 2); \
		test -n "$(OUTPUT)" || (echo "usage: make enhance INPUT=input.wav OUTPUT=enhanced.wav" >&2; exit 2); \
		$(UV) run python examples/grpc-client/client.py \
			--endpoint 127.0.0.1:$${FASTENHANCER_GRPC_PORT:-50051} \
			--input "$(INPUT)" --output "$(OUTPUT)"

compose-test: model ## Build, start, smoke-test, and stop the local GPU Compose stack
	@set -eu; \
		if [ -f deploy/.env ]; then set -a; . deploy/.env; set +a; fi; \
		test -n "$${FASTENHANCER_API_TOKEN:-}" || (echo "FASTENHANCER_API_TOKEN or deploy/.env is required" >&2; exit 2); \
		test -n "$${FASTENHANCER_GPU_DEVICE_ID:-}" || (echo "FASTENHANCER_GPU_DEVICE_ID or deploy/.env is required" >&2; exit 2); \
		trap '$(COMPOSE) --profile test down --remove-orphans' EXIT; \
		FASTENHANCER_GRPC_PORT=0 FASTENHANCER_HTTP_PORT=0 \
			$(COMPOSE) --profile test up --build --abort-on-container-exit --exit-code-from smoke smoke

test-gpu: model ## Run the real-model test suite on the selected GPU
	@set -eu; \
		if [ -f deploy/.env ]; then set -a; . deploy/.env; set +a; fi; \
		test -n "$${FASTENHANCER_GPU_DEVICE_ID:-}" || (echo "FASTENHANCER_GPU_DEVICE_ID or deploy/.env is required" >&2; exit 2); \
		CUDA_VISIBLE_DEVICES="$${FASTENHANCER_GPU_DEVICE_ID}" $(UV) run pytest -m gpu -vv

benchmark: ## Run the real-time load generator and write JSON/Markdown artifacts
	@set -eu; \
		if [ -f deploy/.env ]; then set -a; . deploy/.env; set +a; fi; \
		test -n "$${FASTENHANCER_API_TOKEN:-}" || (echo "FASTENHANCER_API_TOKEN or deploy/.env is required" >&2; exit 2); \
		test -n "$${FASTENHANCER_GPU_DEVICE_ID:-}" || (echo "FASTENHANCER_GPU_DEVICE_ID or deploy/.env is required" >&2; exit 2); \
		$(UV) run python benchmarks/load.py

down: ## Stop the Compose deployment
	@set -eu; \
		if [ -f deploy/.env ]; then set -a; . deploy/.env; set +a; fi; \
		$(COMPOSE) down --remove-orphans

clean: ## Remove only generated build/test caches (model cache is preserved)
	$(UV) run python tools/clean.py
