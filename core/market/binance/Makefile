# Binance Stack Makefile
# 用法: make <target>

SHELL := /bin/bash
PYTHON := $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
PIP := $(if $(wildcard .venv/bin/pip),.venv/bin/pip,pip3)
PYTEST := $(PYTHON) -m pytest
ROOT_PYTHONPATH := PYTHONPATH=src:../../..

.PHONY: help venv install install-dev clean lint lint-root format format-root test test-root test-legacy-lf test-legacy-hf check plan doctor audit canary db-audit start stop status restart

help:
	@echo "Binance Stack 常用命令:"
	@echo ""
	@echo "  环境管理:"
	@echo "    make venv        - 创建顶层虚拟环境"
	@echo "    make install     - 安装顶层运行依赖"
	@echo "    make install-dev - 安装顶层开发依赖"
	@echo "    make clean       - 清理顶层缓存"
	@echo ""
	@echo "  控制面:"
	@echo "    make plan        - 输出 dataset 真相矩阵"
	@echo "    make doctor      - 输出启停前诊断结果"
	@echo "    make audit       - 输出 LF 最近一轮运行审计"
	@echo "    make canary      - 输出 HF canary 计划/状态"
	@echo "    make db-audit    - 输出 Timescale 数据库治理审计"
	@echo "    make start       - 启动 Binance Stack"
	@echo "    make stop        - 停止 Binance Stack"
	@echo "    make status      - 查看 Binance Stack 状态"
	@echo ""
	@echo "  质量门禁:"
	@echo "    make lint        - 顶层 lint"
	@echo "    make test        - 顶层 tests"
	@echo "    make check       - lint + test + verify"

venv:
	@if [ ! -d ".venv" ]; then echo "创建顶层虚拟环境..."; python3 -m venv .venv; .venv/bin/python -m pip install -q --upgrade pip; else echo "顶层虚拟环境已存在"; fi

install: venv
	@echo "安装顶层运行依赖..."
	$(PIP) install -q -e .
	@echo "✅ 顶层依赖安装完成"

install-dev: venv
	@echo "安装顶层开发依赖..."
	$(PIP) install -q -e .[dev]
	@echo "✅ 顶层开发依赖安装完成"

clean:
	@echo "清理顶层缓存..."
	find tests -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find tests -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find tests -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .ruff_cache .mypy_cache build dist .coverage htmlcov
	@echo "✅ 顶层缓存清理完成"

plan:
	$(ROOT_PYTHONPATH) $(PYTHON) -m binance.service_entry plan

doctor:
	$(ROOT_PYTHONPATH) $(PYTHON) -m binance.service_entry doctor

audit:
	$(ROOT_PYTHONPATH) $(PYTHON) -m binance.service_entry audit

canary:
	$(ROOT_PYTHONPATH) $(PYTHON) -m binance.service_entry canary

db-audit:
	./scripts/check_timescale_governance.sh

start:
	./scripts/start.sh start

stop:
	./scripts/start.sh stop

status:
	./scripts/start.sh status

restart:
	./scripts/start.sh restart

lint-root:
	@if $(PYTHON) -m ruff --version >/dev/null 2>&1; then $(ROOT_PYTHONPATH) $(PYTHON) -m ruff check src/ tests/; else echo "⚠️  顶层 ruff 不可用，先运行 make install-dev"; fi

lint: lint-root

format-root:
	@if $(PYTHON) -m ruff --version >/dev/null 2>&1; then $(ROOT_PYTHONPATH) $(PYTHON) -m ruff format src/ tests/; $(ROOT_PYTHONPATH) $(PYTHON) -m ruff check --fix src/ tests/; else echo "⚠️  顶层 ruff 不可用，先运行 make install-dev"; fi

format: format-root

test-root:
	$(ROOT_PYTHONPATH) $(PYTEST) tests/ -v

test-legacy-lf:
	@echo "⚠️  legacy LF tests 已并入顶层 tests/；改跑 test-root"
	@$(MAKE) test-root

test-legacy-hf:
	@echo "⚠️  legacy HF tests 已并入顶层 tests/；改跑 test-root"
	@$(MAKE) test-root

test: test-root

check: lint test
	./scripts/start.sh status >/dev/null
	@echo "✅ Binance Stack 检查完成"
