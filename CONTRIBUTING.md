# Contributing to tokmeter

Thanks for your interest in improving tokmeter! Bug reports, feature ideas, and
pull requests are all welcome.

## Development setup

    git clone https://github.com/cadouille9/tokmeter
    cd tokmeter
    python -m venv .venv
    .venv/bin/pip install -e ".[dev]"

## Running the tests

    .venv/bin/pytest

The full suite runs in well under a second. Please make sure it passes before
opening a pull request; CI runs the same suite on Python 3.11–3.13.

## Opening a pull request

1. Fork the repo and create a branch off `master` (e.g. `git checkout -b fix-something`).
2. Make your change. Add or update a test that covers it.
3. Run `.venv/bin/pytest` and confirm everything passes.
4. Push your branch and open a PR against `master` with a short description of
   the what and the why.

## Guidelines

- Keep changes focused — one logical change per PR is easiest to review.
- Match the surrounding style; the codebase favors small, single-purpose modules.
- The proxy is on every request's hot path: usage capture is best-effort and must
  never break or block a proxied response.

By contributing, you agree that your contributions are licensed under the
project's [MIT License](LICENSE).
