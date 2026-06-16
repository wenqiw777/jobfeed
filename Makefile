.PHONY: test lint fmt quality e2e docker-build docker-quality update-prices web-schema web-build

SOURCE ?= indeed

test:
	pytest

lint:
	ruff check . && ruff format --check . && mypy src/

fmt:
	ruff format . && ruff check --fix .

quality:
	make lint && make test

# Manual full-stack e2e smoke (ephemeral PG + real scan). Not a CI gate.
# Override the source with: make e2e SOURCE=all   (or speedyapply | indeed)
e2e:
	./scripts/e2e_smoke.sh $(SOURCE)

docker-build:
	docker compose build jobfeed-cli

docker-quality: docker-build
	docker compose run --rm jobfeed-cli make quality

update-prices:
	curl -sL https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json \
	  | python3 scripts/update_prices.py

# Regenerate the committed OpenAPI snapshot after changing web routes/DTOs;
# tests/contract/test_web_openapi.py pins it.
web-schema:
	python3 scripts/dump_openapi.py

# Build the SPA bundle (web-ui/dist) that `jobfeed serve` mounts at /.
# web-ui/dist is gitignored build output, so run this before serving the UI.
web-build:
	cd web-ui && npm ci && npm run build
