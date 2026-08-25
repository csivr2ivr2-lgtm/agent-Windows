# Quality baseline and validation

The hardening baseline at commit `88fd3ad7dbf38fd7a6997441f24107509697de49` was 39 discovered tests: 38 passed and the PHP integration test was skipped because PHP CLI was unavailable. `coverage.py` was not installed and package installation was blocked in the local execution environment, so a standard-library `trace --count --missing` measurement was recorded as an explicitly approximate baseline: **82.6% production executable lines (1,039/1,258)**.

After hardening, the same trace method measured **88.4% (1,169/1,323)** across Python production modules. CI installs `coverage.py`, measures line and branch coverage against `src/agent_windows` only, excludes tests/vendor/generated code, publishes `cobertura.xml`, and enforces an honest 80% floor. CI is the authoritative coverage measurement.

Codacy upload uses the official Coverage Reporter action and only runs when the repository secret `CODACY_PROJECT_TOKEN` exists. Create it under **GitHub repository → Settings → Secrets and variables → Actions → New repository secret**, using the project token from **Codacy repository → Settings → Integrations → Project API token**. Never place that value in `.env` or repository files.

Local validation:

```powershell
python -m compileall -q src tests
ruff check src tests
coverage run -m unittest discover -s tests -v
coverage report
```

Cloud calls are mocked. PHP runtime validation remains mandatory during deployment; GitHub Actions runs syntax checks and the PHP integration test on Ubuntu.
