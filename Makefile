.PHONY: all data analysis report validate test clean

PYTHON ?= .venv/bin/python

all:
	$(PYTHON) -m src.main

data:
	$(PYTHON) -m src.main --stage data

analysis:
	$(PYTHON) -m src.main --stage analysis

report:
	$(PYTHON) -m src.main --stage report

validate:
	$(PYTHON) -m src.validate

test:
	$(PYTHON) -m pytest -q

clean:
	@echo "Los rasters son reproducibles; elimine data/processed manualmente si desea regenerarlos."

