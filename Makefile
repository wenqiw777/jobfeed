.PHONY: test lint fmt quality e2e docker-build docker-quality update-prices

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
# Override the source with: make e2e SOURCE=all   (or speedyapply | linkedin-jobspy | indeed)
e2e:
	./scripts/e2e_smoke.sh $(SOURCE)

docker-build:
	docker compose build jobfeed-cli

docker-quality: docker-build
	docker compose run --rm jobfeed-cli make quality

update-prices:
	curl -sL https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json \
	  | python3 scripts/update_prices.py
