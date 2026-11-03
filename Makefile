PYTHON ?= python3
VENV ?= .venv
BIN := $(VENV)/bin

.PHONY: help venv install dev check run test lint format clean

help:
	@echo "Доступные команды:"
	@echo "  make venv     — создать виртуальное окружение .venv"
	@echo "  make install  — установить runtime-зависимости"
	@echo "  make dev      — установить зависимости для разработки"
	@echo "  make check    — проверить подключения к VK и GigaChat"
	@echo "  make run      — запустить бота"
	@echo "  make test     — запустить тесты"
	@echo "  make lint     — проверить код ruff"
	@echo "  make format   — отформатировать код ruff"
	@echo "  make clean    — удалить кэши и артефакты"

venv:
	$(PYTHON) -m venv $(VENV)

install: venv
	$(BIN)/python -m pip install --upgrade pip
	$(BIN)/python -m pip install -r requirements.txt
	$(BIN)/python -m pip install -e .

dev: venv
	$(BIN)/python -m pip install --upgrade pip
	$(BIN)/python -m pip install -r requirements-dev.txt
	$(BIN)/python -m pip install -e .

check:
	PYTHONPATH=src $(BIN)/python -m alina_bot --check

run:
	PYTHONPATH=src $(BIN)/python -m alina_bot

test:
	PYTHONPATH=src $(BIN)/python -m pytest -v

lint:
	$(BIN)/ruff check src tests

format:
	$(BIN)/ruff check --fix src tests

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache .coverage htmlcov build dist *.egg-info
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
