.PHONY: install run evaluate test clean

install:
	pip install -r requirements.txt

run:
	python -m pipeline run

evaluate:
	python -m pipeline evaluate

test:
	python -m pytest tests/ -v

clean:
	rm -rf cache/*.json data/jobs.db data/jobs.csv data/quality_report.json
