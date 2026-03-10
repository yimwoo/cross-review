PYTHON ?= python3.11
VENV_DIR ?= venv
VENV_PYTHON := $(VENV_DIR)/bin/python

.PHONY: help venv install-dev fmt fmt-check flake8 pylint mypy bandit lint test dev-check build check-dist clean

help:
	@echo "make targets:"
	@echo "  venv        - Create a local virtual environment"
	@echo "  install-dev - Install the package with development dependencies"
	@echo "  fmt         - Run black formatting on src/ and tests/"
	@echo "  fmt-check   - Check black formatting without modifying files"
	@echo "  flake8      - Run flake8 linting"
	@echo "  pylint      - Run pylint code analysis"
	@echo "  mypy        - Run mypy type checking"
	@echo "  bandit      - Run bandit security checks"
	@echo "  lint        - Run all linting and static analysis checks"
	@echo "  test        - Run pytest"
	@echo "  dev-check   - Run the full CI-parity local check sequence"
	@echo "  build       - Build wheel and sdist artifacts"
	@echo "  check-dist  - Validate built distribution metadata"
	@echo "  clean       - Remove local caches and build artifacts"

venv:
	$(PYTHON) -m venv $(VENV_DIR)
	$(VENV_PYTHON) -m pip install --upgrade pip

install-dev:
	$(PYTHON) -m pip install -e ".[dev]"

fmt:
	black src/ tests/

fmt-check:
	black --check src/ tests/

flake8:
	flake8 src/ tests/

pylint:
	pylint src/cross_review/

mypy:
	mypy src/cross_review/

bandit:
	bandit -r src/cross_review/

lint: flake8 pylint mypy bandit

test:
	pytest tests/ -v

dev-check: fmt-check flake8 pylint mypy bandit test

build:
	rm -rf dist
	$(PYTHON) -m build

check-dist: build
	$(PYTHON) -m twine check dist/*

clean:
	find . -type d -name "__pycache__" -prune -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -prune -exec rm -rf {} +
	find . -type d -name ".mypy_cache" -prune -exec rm -rf {} +
	rm -rf build dist *.egg-info
