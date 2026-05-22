.PHONY: test lint fmt quality docker-build docker-quality

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
