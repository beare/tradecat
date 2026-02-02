.PHONY: help venv install install-dev lock run start stop status lint format test clean

PY ?= python3
VENV_DIR ?= .venv
VENV_PY := $(VENV_DIR)/bin/python
VENV_PIP := $(VENV_DIR)/bin/pip

help:
	@echo "Targets:"
	@echo "  make venv         创建虚拟环境"
	@echo "  make install      安装运行依赖"
	@echo "  make install-dev  安装开发依赖"
	@echo "  make lock         生成 requirements.lock.txt"
	@echo "  make run          前台运行（调试）"
	@echo "  make start        后台启动"
	@echo "  make stop         停止后台进程"
	@echo "  make status       查看进程状态"
	@echo "  make lint         ruff lint"
	@echo "  make format       ruff format"
	@echo "  make test         pytest"
	@echo "  make clean        清理缓存"

venv:
	@$(PY) -m venv $(VENV_DIR)
	@$(VENV_PIP) install -U pip

install: venv
	@$(VENV_PIP) install -r requirements.txt

install-dev: install
	@$(VENV_PIP) install -r requirements-dev.txt

lock: install
	@$(VENV_PIP) freeze | LC_ALL=C sort > requirements.lock.txt
	@echo "已生成 requirements.lock.txt"

run: install
	@$(VENV_PY) -m src --mode ws --tool ccxt --seconds 30

start: install
	@./scripts/start.sh start

stop:
	@./scripts/start.sh stop

status:
	@./scripts/start.sh status

lint: install-dev
	@$(VENV_PY) -m ruff check src tests

format: install-dev
	@$(VENV_PY) -m ruff format src tests

test: install-dev
	@$(VENV_PY) -m pytest -q

clean:
	@rm -rf .ruff_cache .mypy_cache .pytest_cache __pycache__ logs pids

