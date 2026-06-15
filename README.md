# tokmeter

Transparent local proxy that logs LLM token usage per model to SQLite, with
cost-savings reports. Sits in front of any OpenAI-compatible server (llama.cpp, ollama).

## Install

    python -m venv .venv
    .venv/bin/pip install -e .

## Run

    .venv/bin/tokmeter serve        # listens on 127.0.0.1:8079 -> 127.0.0.1:8080

Override with env vars: `TOKMETER_PORT`, `TOKMETER_UPSTREAM`, `TOKMETER_HOST`.

### Point your clients at the proxy

Change your client base URL from the backend port to the proxy:

    OPENAI_BASE_URL=http://127.0.0.1:8079/v1

Streaming note: tokmeter sets `stream_options.include_usage=true` on streaming
chat/completions requests so the backend reports token counts; clients receive the
standard final usage chunk.

## Run as a service (systemd --user)

    cp systemd/tokmeter.service ~/.config/systemd/user/
    systemctl --user daemon-reload
    systemctl --user enable --now tokmeter
    journalctl --user -u tokmeter -f

## Reports

    .venv/bin/tokmeter report                 # by model + totals
    .venv/bin/tokmeter report --by day
    .venv/bin/tokmeter report --since 2026-06-01 --until 2026-06-15
    .venv/bin/tokmeter report --by model --csv usage.csv
    .venv/bin/tokmeter models                 # models seen + pricing state

`default` in the report's Pricing column (and `[default]` in `models`) means the
model has no entry in `~/.config/tokmeter/pricing.yaml` and is priced at the global
default. Pricing keys are matched case-insensitively and ignore a trailing `.gguf`,
so `Qwen3.6-27B-UD-Q6_K_XL` matches the server-reported `Qwen3.6-27B-UD-Q6_K_XL.gguf`.

## Compare against cloud models

Estimate what your recorded local usage would have cost on cloud models (i.e. your
savings vs each), using the `references:` section of `pricing.yaml`:

    .venv/bin/tokmeter compare                      # totals: one row per reference
    .venv/bin/tokmeter compare --since 2026-06-01   # same filters as report
    .venv/bin/tokmeter compare --model Qwen3.6-27B-UD-Q6_K_XL.gguf
    .venv/bin/tokmeter compare --by-model           # matrix: local models x references

The shipped reference prices are **example placeholders** — edit
`~/.config/tokmeter/pricing.yaml` with current figures from
https://www.anthropic.com/pricing (and add other providers as needed).

A reference is only used if both `input_per_1m` and `output_per_1m` are present and
are numbers `>= 0`. An invalid or missing price does **not** silently become `$0.00`:
that reference is skipped and `compare` prints a warning naming it, so a typo can't
quietly understate your costs.

## Pricing

Copy the example and edit cloud-equivalent prices (USD per 1M tokens):

    mkdir -p ~/.config/tokmeter
    cp config/pricing.yaml ~/.config/tokmeter/pricing.yaml
