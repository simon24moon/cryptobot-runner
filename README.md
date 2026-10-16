# CryptoBot public runner

Generic execution code for an authenticated, separately operated CryptoBot service.

This public runner is `simon24moon/cryptobot-runner`. The original private service repository stays private. This runner receives authenticated jobs from the private service, reads public market data, captures Crypto Bubbles, and sends results to the configured Slack destination.

## Files that would be public

- `.github/workflows/cryptobot.yml`: manually dispatched workflow, standard Ubuntu 24.04 runner, pinned third-party Actions, read-only default GitHub token.
- `runner/*.py`, `runner/capture.cjs`: generic job execution, public market lookup, capture and Slack delivery code.
- `runner/config.example.json`, `runner/bubble_categories.json`: public chart settings and category order.
- `package.json`, `pnpm-lock.yaml`: exact JavaScript dependency versions and integrity hashes. Python uses only its standard library; `requirements.txt` documents that no pip packages are installed.
- `.github/CODEOWNERS`, `.github/dependabot.yml`, `.gitignore`, `SECURITY.md` and this README.

There are no tokens, personal channel/workspace identifiers, private watchlist IDs, actual watchlists, Worker URL, local state, screenshots, or OAuth code in this candidate. All destination values and tokens are configured through GitHub Actions Secrets. Capture artifacts are not uploaded to the public repository. Workflow execution logs will be public; the normal workflow does not print quote tables, private watchlists, tokens, or upload URLs.

## Credential boundaries

The public workflow receives a separate `EXECUTOR_TOKEN`, not the administrative Runner token. It can claim already-issued jobs, record their progress and completion, and read the one configured watchlist needed for its report. It cannot create arbitrary jobs, read administrative status, change diagnoses, or reconnect TradingView. The server still requires the per-job claim token for progress and completion. The executor credential is provisioned separately from the administrative token.

The Slack bot token remains an Actions Secret because this candidate uploads images directly. Its existing bot scopes do not include trading or account withdrawal. TradingView OAuth tokens, Slack Signing Secret, Cloudflare deployment credentials, GitHub dispatch credentials and the administrative Runner token stay out of the public runner. This is risk reduction, not an assertion that a compromised runner could not misuse its runtime credentials.

Secrets are supplied only to the execution step, after dependency installation. Browser installation and capture subprocesses use an environment allowlist that omits service credentials. The sending subprocess has its own allowlist and receives only its delivery credentials and current job claim. Authenticated service requests and signed image uploads reject redirects. Image uploads accept only HTTPS `files.slack.com/upload/v1/` destinations; linked files, non-PNG files and files over 10 MiB are rejected. Checkout does not persist its GitHub credential. Third-party Actions are pinned to commit IDs; dependency versions and the JavaScript lockfile are fixed; package installation scripts are disabled for the JavaScript install.

A repository writer or a compromised dependency that executes in the trusted sending process could still misuse the Secrets. Log masking is not a substitute for avoiding secret output. Keep repository write access restricted, enable account two-factor authentication, review dependency/workflow changes and revoke/replace any credential suspected of exposure. No current exposure has been established by this source review.

Only `workflow_dispatch` on `main` is enabled. No pull request workflow receives secrets. The public CLI requires an existing job ID and cannot create jobs or print report/watchlist previews. Server jobs determine the actual command and destination; workflow input never becomes shell code. Public repository standard-runner execution time is free under GitHub's current policy; this does not guarantee exemption from account-wide payment restrictions or future policy changes. A real execution must pass before the existing service is switched. No payment settings will be changed.

If the existing repository-scoped dispatch token cannot dispatch this new repository, a token scoped to this runner's Actions permission is needed. No token scope will be expanded silently. The existing account authentication can be used to create and configure the authorized repository, but it will not be copied into the Worker as a broad dispatch credential.
