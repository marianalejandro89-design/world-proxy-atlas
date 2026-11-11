# Contributing

Contributions are welcome. Please submit focused pull requests.

Before adding a feed, record its stable machine-readable URL, owner, refresh evidence, protocol semantics, attribution terms and known upstream overlap in config/sources.toml and docs/SOURCE_DISCOVERY.md. Do not scrape HTML pages without a documented feed interface. Keep response limits and protocol tests in place.

Run:

    python -m pip install -e ".[test]"
    python -m pytest -q
    python -m ruff check src tests

Never commit credentials, API keys, personal data or unvalidated proxy candidates as verified output.
