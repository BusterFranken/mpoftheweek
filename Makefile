PY ?= .venv/bin/python

.PHONY: refresh pipeline build test dev setup

## Full refresh: pull data, then build the static site.
refresh: pipeline build

pipeline:
	$(PY) -m pipeline.run

build:
	cd site && npm run build

test:
	$(PY) -m pytest pipeline/tests -q

dev:
	cd site && npm run dev

## One-time local setup.
setup:
	python3.12 -m venv .venv
	.venv/bin/pip install -r pipeline/requirements.txt
	cd site && npm install
