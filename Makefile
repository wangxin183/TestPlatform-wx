.PHONY: install install-dev init-db test run clean

VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
PIP_MIRROR := https://pypi.tuna.tsinghua.edu.cn/simple

install:
	$(PIP) install -i $(PIP_MIRROR) -r requirements.txt

install-dev: install
	$(PIP) install -i $(PIP_MIRROR) -r requirements-dev.txt

init-db:
	$(PYTHON) scripts/init_db.py

test:
	$(PYTHON) -m pytest tests/ -v

run:
	$(PYTHON) -m uvicorn src.main:app --reload --host 0.0.0.0 --port 8999

clean:
	rm -rf storage/test_platform.db logs/*.log
	find . -type d -name __pycache__ -exec rm -rf {} +
