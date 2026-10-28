# Contributing

Contributions are welcome: bug reports, fixes, new read tools, better
documentation, and especially new entries for
[docs/API-DEVIATIONS.md](docs/API-DEVIATIONS.md) when you catch Auvik's API
doing something its documentation says it does not.

## Before you start

- **Small fixes:** open a pull request directly.
- **New tools or behaviour changes:** open an issue first so we can agree on
  the shape before you write the code.
- **Anything that writes to Auvik:** open an issue first, always. This server
  has exactly one write (`auvik_dismiss_alert`) and a test that enforces a
  single `client.post(` call site. A new write is a design decision, not a
  pull request, and it will not be merged without that conversation.

## Ground rules

These are the design principles in the README, and review holds every change
to them:

1. **"Nothing found" and "couldn't reach Auvik" stay different answers.** Every
   tool returns `data_status` of `ok`, `unavailable` or `refused`, and an
   `unavailable` response carries no data keys.
2. **Responses stay under 500 KB**, with `limit`, `returned_count`,
   `truncated` and a full-match count on every list tool.
3. **Chat-client-safe schemas.** Primitive parameter types, a default on every
   optional parameter, no `Optional` or unions. `tests/test_schema_rules.py`
   checks this.
4. **Vendor text is passed through, never invented.**
5. **No credential ever reaches a log, fixture, response or commit.**

## Development setup

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt -c constraints.txt
.venv/bin/python -m pytest -q
```

On Windows use `py -3.12` and `.venv\Scripts\python.exe`.

The suite runs entirely offline and must pass before a pull request is
reviewed. GitHub Actions runs it on every pull request, and `main` will not
accept a merge until it passes. It blocks real sockets and real credential loads, so you do not need
an Auvik tenant to contribute.

## Fixtures and test data

- Committed fixtures under `tests/fixtures/synthetic/` are **invented**. Change
  them through `tests/build_synthetic_fixtures.py`, never by hand-editing in
  real data.
- `tests/fixtures/recorded/` is gitignored on purpose. Never commit recorded
  responses from a real tenant: they contain device names, IPs and serials.
- New behaviour gets a test. A bug fix gets a test that fails without the fix.

## Pull requests

- One change per pull request, with a description of what it does and how you
  checked it.
- If you ran anything against a live tenant (for example
  `scripts/live_smoke.py`, which is GET-only), say so and say what you saw.
  Do not paste tenant data into the pull request.
- Keep pinned versions in `constraints.txt` unless the change is a dependency
  update.

## Reporting a security issue

Please do not open a public issue for a security problem. Report it privately
through GitHub instead: the repository's **Security** tab, then **Report a
vulnerability**
([direct link](https://github.com/GTalksTech/auvik-mcp/security/advisories/new)).
Only you and the maintainer can see the report. If you cannot use GitHub,
email garrett@gtalkstech.com.

## License

By contributing you agree that your contribution is licensed under the
repository's [MIT license](LICENSE).
