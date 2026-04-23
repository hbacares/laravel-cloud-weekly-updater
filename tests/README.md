# Tests

## Running Tests

Install dev dependencies:
```bash
pip install -e ".[dev]"
```

Run all tests:
```bash
pytest
```

Run specific test file:
```bash
pytest tests/test_scheduler.py
```

Run with coverage:
```bash
pytest --cov=websiteupdater --cov-report=html
```

## Test Organization

- `test_scheduler.py` - Scheduler logic (due projects, week calculations)
- `test_cleanup.py` - Artifact cleanup functionality
- `test_lockfile.py` - File-based locking mechanism
- `test_retry.py` - Retry decorator and backoff logic

## Coverage

Current test coverage focuses on core utility functions that don't require external dependencies (Laravel Cloud API, git, composer, npm).

Integration tests for the full pipeline would require:
- Mock Laravel Cloud API server
- Test git repositories
- Mock composer/npm installations
- Playwright browser automation fixtures
