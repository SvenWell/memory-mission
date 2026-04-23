.PHONY: install test lint typecheck dev check clean

install:
	pip install -e '.[dev]'

install-all:
	pip install -e '.[db,memory,integrations,runtime,dev]'

test:
	pytest -v

test-cov:
	pytest --cov=memory_mission --cov-report=term-missing

lint:
	ruff check src/ tests/
	ruff format --check src/ tests/

lint-fix:
	ruff check --fix src/ tests/
	ruff format src/ tests/

typecheck:
	mypy src/

check: lint typecheck test

dev:
	python -m memory_mission --help

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name '*.egg-info' -exec rm -rf {} +
	find . -type d -name '.pytest_cache' -exec rm -rf {} +
	find . -type d -name '.mypy_cache' -exec rm -rf {} +
	find . -type d -name '.ruff_cache' -exec rm -rf {} +
