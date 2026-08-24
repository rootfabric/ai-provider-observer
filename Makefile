.PHONY: brief status resume diagnose validate validate-active validate-ready portable-check hygiene unit selftest report final-report demo
brief:
	python3 scripts/harness/control.py brief
status:
	python3 scripts/harness/control.py status
resume:
	python3 scripts/harness/control.py resume
diagnose:
	python3 scripts/harness/control.py diagnose
validate:
	python3 scripts/harness/control.py validate
validate-active:
	python3 scripts/harness/control.py validate-active
validate-ready:
	python3 scripts/harness/control.py validate-ready
portable-check:
	python3 scripts/harness/control.py portable-check
hygiene:
	python3 scripts/harness/control.py hygiene
unit:
	@if [ -x .venv/bin/python ]; then .venv/bin/python -m pytest tests -q; else python3 -m pytest tests -q; fi
selftest:
	python3 scripts/harness/control.py selftest
report:
	python3 scripts/harness/control.py report
final-report:
	python3 scripts/harness/control.py final-report
demo:
	python3 scripts/harness/control.py demo
