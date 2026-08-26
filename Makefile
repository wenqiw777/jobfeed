.PHONY: test test-postgres lint fmt quality update-prices web-schema web-build dev

# Run the API and Vite in one foreground session. Ctrl-C stops both.
dev:
	PATH="$(CURDIR)/.venv/bin:$$PATH" python3 scripts/dev.py

test:
	pytest

# Explicit compatibility/migration lane. The default pytest addopts excludes it.
test-postgres:
	JOBFEED_REQUIRE_POSTGRES=1 JOBFEED_CONTRACT_BACKEND=postgres pytest -m postgres tests/contract tests/integration tests/store tests/e2e/test_legacy_import.py -o "addopts=" -v --tb=short

lint:
	ruff check . && ruff format --check . && mypy src/

fmt:
	ruff format . && ruff check --fix .

quality:
	make lint && make test

update-prices:
	curl -sL https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json \
	  | python3 scripts/update_prices.py

# Regenerate the committed OpenAPI snapshot after changing web routes/DTOs;
# tests/contract/test_web_openapi.py pins it.
web-schema:
	python3 scripts/dump_openapi.py

# Rebuild the committed SPA bundle that `jobfeed serve` mounts at /.
# End users receive this artifact and never need Node/pnpm.
web-build:
	cd web-ui && pnpm install --frozen-lockfile && pnpm build
