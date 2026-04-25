.PHONY: install test cov lint fix download clean

install:
	python -m venv .venv 2>/dev/null || true
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install -e ".[dev]"

test:
	.venv/bin/pytest -q

cov:
	.venv/bin/pytest --cov=obscura --cov-report=term-missing -q

lint:
	.venv/bin/ruff check src tests

fix:
	.venv/bin/ruff check --fix src tests
	.venv/bin/ruff format src tests

download:
	@if [ -z "$(DATE)" ]; then echo "usage: make download DATE=12302019"; exit 1; fi
	.venv/bin/obscura download --date $(DATE) --out data/itch

clean:
	rm -rf .venv .pytest_cache .ruff_cache .hypothesis .coverage build dist *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} +
