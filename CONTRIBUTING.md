# Contributing

agent-Windows targets Windows 11 machines with modest hardware. Keep the runtime dependency-free unless a dependency has a measured, documented benefit.

## Development setup

```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m compileall -q src tests
ruff check src tests
coverage run -m unittest discover -s tests -v
coverage report
```

Tests must mock cloud APIs and must not require provider keys. Add failure and boundary tests with behavior changes. Never commit `.env`, recordings, transcripts, databases, upload spools, or credentials.

Pull requests should be small, preserve offline behavior, explain security/resource tradeoffs, and update README/config examples when behavior changes. Run the full suite on Windows where possible. PHP Relay changes also require `php -l` and deployment-environment integration tests.
