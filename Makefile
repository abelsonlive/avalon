.PHONY: lint test build upload

lint:
	uv run ruff check .

test:
	uv run --extra test pytest

build:
	rm -rf dist
	uv run python -m build

upload: build
	uv run twine upload dist/*
