# Contributing

Thanks for helping! Bug reports, API captures and pull requests are welcome.

## Reporting API changes

The integration depends on the internal Access API. If something stops working after an
Access update, please open an issue and attach:

- the Access version (*UniFi Access → Settings → General*),
- the **diagnostics** download of the integration (redacted),
- if you can, the request and response that the Access web UI sends for the same action
  (browser dev tools → Network, path `/proxy/access/api/v2/...`).
  Remove names, phone numbers and e-mail addresses first.

## Development

```bash
python3.13 -m venv .venv && . .venv/bin/activate
pip install -r requirements_test.txt ruff
ruff check . && ruff format --check .
pytest
```

Tests run against an in-memory fake console (`tests/conftest.py`) built from a real
topology capture (`tests/fixtures/topology.json`).

Pull requests:

- Keep changes focused; add or update tests.
- Update `CHANGELOG.md` and the translations (`strings.json`, `translations/en.json`, `translations/de.json`).
- CI runs hassfest, the HACS validation, ruff and pytest.

## Releases

1. Bump `version` in `custom_components/advanced_uaccess/manifest.json`.
2. Update `CHANGELOG.md`.
3. Create a GitHub release with tag `vX.Y.Z` (the release workflow checks that tag and manifest match).
