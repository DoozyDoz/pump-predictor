.PHONY: init daily backtest universe

init:
	python -m src.main init

universe:
	python -m src.main universe

daily:
	python -m src.main daily

backtest:
	python -m src.main backtest

backtest-fast:
	python -m src.main backtest --limit 20

install:
	pip install -r requirements.txt
