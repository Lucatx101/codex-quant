PYTHON ?= python3
PYTHONPATH := src

.PHONY: install lint typecheck test audit check data-universe data-daily-smoke data-quotes-smoke

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

data-universe:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m hose_quant.cli data fetch-universe --exchange HOSE

data-daily-smoke:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m hose_quant.cli data backfill-daily --symbols FPT,HPG,VCB --start 2025-01-01 --end 2026-07-03

data-quotes-smoke:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m hose_quant.cli data snapshot-quotes --symbols FPT,HPG,VCB

check: lint typecheck test
