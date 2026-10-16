# Security boundaries

This is generic execution code. Production state, personal configuration,
OAuth credentials, screenshots and private repository history do not belong
in this repository. The reviewed first release is checked locally against a
file and content allowlist maintained in the separate private repository.
That gate also scans known private values and common token patterns, including
URL/base64 encodings. It is not a proof that every possible secret format or
software vulnerability can be detected.

## Runtime

- A separate executor credential may claim existing service jobs, record
  progress/completion and fetch the configured watchlist. Administrative and
  deployment credentials never enter this runner. The server checks per-job
  ownership and the client refuses other service endpoints.
- The destination and bot identity must match the configured Secrets.
  Browser subprocesses receive no service credentials in their environment.
  This environment separation is not a sandbox between mutually hostile
  processes running as the same operating-system user.
- Slack upload/message calls record delivery intent with the private service.
  Unknown results require review instead of automatically posting again.
- No capture or report artifact is uploaded to GitHub. Logs contain operation
  progress and error categories, not report contents, watchlists, tokens or
  signed upload URLs. Public workflow metadata can reveal execution times
  and opaque job identifiers.
- A repository writer or compromised trusted dependency can still alter
  code to access runtime Secrets. Account security and review remain necessary.
  Secret masking alone does not prevent deliberate exfiltration.

## Before enabling a new repository

The files above do not activate GitHub account/repository controls themselves.
Before production Secrets or dispatch are enabled, the operator must:

1. Restrict write/admin access, confirm account two-factor authentication,
   and protect `main` with review rules where available. `CODEOWNERS` names
   the reviewer; it does not enforce review without repository rules.
2. Enable secret scanning, push protection, dependency alerts and security
   updates. Dependency update pull requests must be reviewed; no automatic
   merge is configured.
3. Keep the workflow token read-only, disable Actions PR approval, and limit
   allowed Actions to the reviewed official Actions. Never enable a workflow
   that checks out untrusted code with these Secrets.
4. Issue a fresh executor credential and use individual Actions Secrets for
   the six values referenced in the workflow. Never reuse the administrative
   credential or store a JSON bundle containing multiple secrets.
5. Grant the private dispatch credential only Actions access to the selected
   runner repository. Do not install a personal, broadly privileged credential
   in the private service as a shortcut.
6. Validate a real dispatched job, then inspect its public logs and artifact
   list for unexpected output before switching the production dispatcher.

## If a credential is suspected to be exposed

Disable dispatch and running workflows, revoke/replace the affected credential,
and inspect service delivery records. Remove any exposed public logs/artifacts.
Deleting source alone cannot invalidate an exposed credential. Coordinate
through GitHub private vulnerability reporting when enabled; do not paste
credentials into public issues or pull requests.

References: [GitHub Actions security](https://docs.github.com/en/actions/reference/security/secure-use),
[Actions Secrets](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets),
[Slack external uploads](https://docs.slack.dev/reference/methods/files.getUploadURLExternal/).
