.PHONY: test functional lint mdlint build

test:
	uv run --extra test pytest exordos_usage/tests/unit

functional:
	uv run --extra test pytest exordos_usage/tests/functional

lint:
	uv run --extra test ruff check .
	uv run --extra test ruff format --check .

mdlint:
	markdownlint-cli2 --config .markdownlint.yaml "**/*.md" "!.venv" "!.tox"

build:
	exordos build -f
