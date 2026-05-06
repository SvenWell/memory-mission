.PHONY: install test lint typecheck dev check clean release-check tag

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

# Release tooling. Usage: make tag VERSION=v0.1.7
# Refuses to tag if pyproject.toml [project].version doesn't match VERSION.
# Background: v0.1.5 shipped with stale pyproject metadata (still 0.1.3),
# producing wheels labeled 0.1.3. v0.1.6 fixed the drift and added this guard.
release-check:
	@if [ -z "$(VERSION)" ]; then echo "Usage: make release-check VERSION=vX.Y.Z"; exit 1; fi
	@python scripts/check_release_version.py "$(VERSION)"

tag: release-check
	git tag -a "$(VERSION)" -m "Release $(VERSION)"
	@echo "Tag $(VERSION) created locally. Push with: git push origin $(VERSION)"
