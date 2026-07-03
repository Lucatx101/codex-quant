PYTHON ?= python3
PYTHONPATH := src

.PHONY: install lint typecheck test audit check

install:
	$(PYTHON) -m pip install -e ".[dev]"

lint:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m ruff check .

typecheck:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m mypy src

test:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m pytest

audit:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m hose_quant.cli audit-data

check: lint typecheck test
