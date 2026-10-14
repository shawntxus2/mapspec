VENV := .venv
PY   := $(VENV)/bin/python
PIP  := $(VENV)/bin/pip
DBT  := $(abspath $(VENV))/bin/dbt

PG_NAME  := mapspec_pg
PG_PORT  := 55432
PG_IMAGE := postgres:16-alpine

.PHONY: help setup validate emit graph accept accept-postgres parse-tier2 \
        pg-up pg-down dbt check stale clean distclean

help:
	@echo "make setup     create the venv and install dbt, duckdb, sqlglot, psycopg2 (arm64 safe)"
	@echo "make check     validate -> emit -> graph -> acceptance (DuckDB + PostgreSQL) -> tier-2 parse"
	@echo "make dbt       emit the acceptance specs and run a real dbt build"
	@echo "make validate  schema plus the semantic checks"
	@echo "make emit      dbt models, tests and coverage.md from the reference specs"
	@echo "make graph     lineage JSON and the self-contained review page"
	@echo "make accept    run the generated SQL on DuckDB and assert what it claims"
	@echo "make accept-postgres  same specs and assertions against PostgreSQL in a container"
	@echo "make parse-tier2      parse-check the transpiled SQL for the tier-2 platforms"
	@echo "make clean     remove generated output, keep the venv"

# dbt pins its dependencies hard. Keep it out of any environment that also carries
# PyTorch or the inference containers, or an upgrade here breaks something there.
# The versions are pinned to the set this suite was verified against. Unpinned, the next
# clean machine can resolve a different set and the verification stops meaning anything.
# dbt-adapters and dbt-common are pinned too: that is where dbt's behavior actually moves.
# dbt_utils stays in packages.yml, which already pins it.
setup:
	python3 -m venv $(VENV)
	$(PIP) install --quiet --upgrade pip
	$(PIP) install --quiet \
		dbt-core==1.12.5 dbt-duckdb==1.11.0 dbt-adapters==1.24.5 dbt-common==1.39.0 \
		duckdb==1.5.6 sqlglot==30.22.0 psycopg2-binary==2.9.13 pyyaml jsonschema
	cd dbt && $(DBT) deps --profiles-dir .
	@echo "ready. next: make check"

validate:
	$(PY) validate.py

# The reference specs under models/ point at illustrative URIs, so they are emitted for
# inspection and coverage rather than executed.
emit:
	$(PY) emit.py --out build

graph:
	$(PY) graph.py

accept:
	$(PY) emit.py --models acceptance/models --out acceptance/build
	$(PY) acceptance/run.py

# PostgreSQL is the second tier-1 platform: the same specs and the same assertions against a
# real engine, which is the only way to prove the transpiler preserved meaning. The container
# is brought up and torn down around the run; without docker or psycopg2 the runner skips
# rather than fails, so a machine without PostgreSQL is unverified, not blocked.
accept-postgres:
	$(PY) validate.py --models acceptance/models --platform postgres
	$(PY) emit.py --models acceptance/models --out acceptance/build_pg --platform postgres
	$(MAKE) pg-up
	$(PY) acceptance/run_postgres.py && $(PY) acceptance/differential.py; rc=$$?; $(MAKE) pg-down; exit $$rc

pg-up:
	-@docker rm -f $(PG_NAME) >/dev/null 2>&1
	# POSTGRES_PASSWORD: local test-container credential only, used inside the container; not a real secret.
	-@docker run -d --name $(PG_NAME) -e POSTGRES_PASSWORD=mapspec -e POSTGRES_DB=mapspec \
	   -p $(PG_PORT):5432 $(PG_IMAGE) >/dev/null 2>&1
	-@for i in $$(seq 1 30); do docker exec $(PG_NAME) pg_isready -U postgres >/dev/null 2>&1 && break; sleep 1; done

pg-down:
	-@docker rm -f $(PG_NAME) >/dev/null 2>&1

# Tier-2 platforms (Snowflake, SQL Server, Oracle, DB2) are parse-checked only, never run.
parse-tier2:
	$(PY) acceptance/parse_tier2.py

check: validate emit stale graph accept accept-postgres parse-tier2
	@echo ""
	@echo "all green"

# The repo commits the generated dbt project (models, tests, landing, seeds, coverage.md)
# so a visitor can read the SQL on GitHub without cloning. CLAUDE.md also says that SQL is
# disposable: `emit` overwrites it from the specs, and nobody hand-edits it. Those two facts
# stay consistent only if the committed output always equals what the current specs emit, so
# the build re-emits and fails if the working tree moved. A diff here means someone changed a
# spec or the emitter without re-running `make dbt` and committing dbt/.
stale:
	$(PY) emit.py --models acceptance/models --out dbt
	cp acceptance/seeds/*.psv dbt/seeds/
	@git diff --exit-code -- dbt/models dbt/seeds dbt/tests dbt/landing dbt/coverage.md \
	  || { echo ""; \
	       echo "ERROR: generated dbt output is stale. Run 'make dbt' and commit dbt/."; \
	       exit 1; }

# The dbt project is built from the acceptance specs, whose sources are real files.
dbt:
	$(PY) validate.py --models acceptance/models
	$(PY) emit.py --models acceptance/models --out dbt
	cp acceptance/seeds/*.psv dbt/seeds/
	cd dbt && $(DBT) build --profiles-dir .

clean:
	rm -rf build acceptance/build acceptance/build_pg dbt/models/*.sql dbt/models/*.yml \
	       dbt/seeds/*.psv dbt/tests/*.sql dbt/target dbt/logs warehouse.duckdb \
	       ui/review.html ui/graph.json
	find . -name __pycache__ -type d -prune -exec rm -rf {} +

distclean: clean
	rm -rf $(VENV) dbt/dbt_packages
