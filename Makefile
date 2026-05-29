.PHONY: test lint fmt quality docker-build docker-quality update-prices

test:
	pytest

lint:
	ruff check . && ruff format --check . && mypy src/

fmt:
	ruff format . && ruff check --fix .

quality:
	make lint && make test

docker-build:
	docker compose build jobfeed-cli

docker-quality: docker-build
	docker compose run --rm jobfeed-cli make quality

update-prices:
	curl -sL https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json \
	  | python3 scripts/update_prices.py
