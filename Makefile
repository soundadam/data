.PHONY: check lint test requirements image

EXPORT = uv export --frozen --no-dev --no-emit-project --no-header --quiet

check: lint test
	$(EXPORT) | diff -q - requirements.txt

lint:
	uv run --frozen ruff check src tests
	uv run --frozen ruff format --check src tests
	for f in src/dataservice/static/*.js; do node --check $$f || exit 1; done

test:
	uv run --frozen pytest

requirements:
	$(EXPORT) > requirements.txt

image:
	docker build -t dataservice:dev .
