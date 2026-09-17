# Reliability Rules

- Every long-running operation exposes explicit state, progress, cancellation, and ownership.
- `AuthenticatedOperationCoordinator` is the Application-layer SSOT for EC2, RDS, Secrets,
  and S3 recovery: MFA resumes the exact original request at most once after successful refresh.
- Pending MFA challenges and their in-memory callables are capped at 128. After expired entries
  are swept, a full coordinator rejects only the new request with
  `auth.operation.capacity_exceeded`; it never invalidates an existing user dialog. A profile
  with reusable credentials bypasses challenge capacity.
- Token refresh is serialized per profile within a process and rechecks reusable credentials
  inside that profile lock, so concurrent requests issue STS at most once. Different profiles
  retain independent refresh locks and can proceed in parallel.
- Expired, malformed, undecryptable, or identity-mismatched cached sessions are disposable:
  delete the bad row and enter the normal MFA flow instead of terminating the feature.
- `AuthenticationService.status()` is an expiry-only local projection for GUI startup and never
  decrypts credentials or calls AWS. If an authenticated feature nevertheless returns
  `auth.mfa_required`, that feature result is authoritative: the shared coordinator explicitly
  discards only the cached temporary session and creates an MFA challenge. Protected long-lived
  profile credentials remain unchanged, and the original feature request resumes exactly once.
- SQLite uses a busy timeout, short transactions, and versioned migrations.
- Check local port availability before starting the Session Manager Plugin.
- On cancel, failure, or normal exit, owned plugin processes and SSM sessions are cleaned up.
- A managed SSM runner retains temporary credentials only while its process is running. Launch or
  progress-observer failure reclaims the process and AWS session; poll, natural exit, explicit stop,
  and `EndSession` failure return one terminal snapshot and then remove all owned state. When more
  than one boundary fails, the first typed error remains authoritative and later cleanup failures
  are appended only as diagnostics.
- EC2 external sessions and GUI-owned RDS tunnels preserve the caller's single `OperationContext`
  identity, progress reporter, and cancellation token. Their public coordinators return one flat
  `OperationResult` whose value is a handle/result DTO, never another `OperationResult`.
- EC2 external terminal sessions transfer ownership to the user after the helper reports the
  real Plugin PID; GUI shutdown does not terminate them. GUI-owned RDS tunnels remain subject
  to `stop_all`, which attempts every owned tunnel even when an earlier cleanup fails.
- The external terminal helper lets the already-started Plugin receive console `Ctrl+C` but ignores
  that signal itself, so interrupting a remote foreground command does not close the EC2 shell.
- Profile deletion queries both EC2 and RDS ownership sources before confirmation. Active deletion
  requires an explicit stop-and-delete choice, attempts both cleanup groups even if the first one
  fails, and never removes the profile when observation or cleanup is incomplete.
- External terminal handshake failures close the named-pipe listener and connection, stop the
  dispatch process when possible, and cause the untransferred AWS session to be ended.
- Retry only typed retryable failures; never retry authentication or permission failures blindly.
- A feature failure must not terminate unrelated GUI functions.
- `ListSecrets` is an optional, explicit metadata operation. Permission or network failure leaves
  direct name/ARN input and `GetSecretValue` available, so catalog access cannot gate lookup.
- EC2 inventory joins paginated EC2 and SSM responses by instance ID. Missing or stale SSM metadata is
  represented as Offline/Unknown; EC2 permission failure preserves the legacy SSM Online fallback.
- EC2 StartInstances and RebootInstances are asynchronous. After DryRun authorization and explicit
  confirmation, one request is issued, duplicate actions are blocked, and bounded polling reports
  convergence or a timeout that clearly states AWS may still be completing the request.
- SSM Run Command Secret lookup polls eventual-consistency states with a bounded timeout and supports
  cancellation. Failed, TimedOut, Cancelled and DeliveryTimedOut results become typed errors; no state
  is treated as success until one successful invocation yields a bounded output.
- The explicit Secret terminal fallback is tracked by the existing external EC2 session lifecycle.
  StartSession/document/plugin failures remain typed errors and show the required permission or
  manual recovery guidance without changing the current profile or Secret result.
- Optional S3 Bucket catalog failure changes only the selector to direct input and never disables
  saved locations, object listing, overwrite preflight or upload.
- S3 batch download expands selected prefixes with paginated listing, removes nested duplicate keys,
  preserves relative paths, and rejects destination traversal before transfer. Each file uses a
  uniquely named adjacent temporary file, commits only after GetObject succeeds, and
  removes partial files on failure or cancellation.
- Correlation IDs connect user-visible errors and masked diagnostic logs.
- Log destination and level are versioned SQLite settings shared by GUI and CLI. Reconfiguration
  must install the new rotating handler successfully before the setting is committed, so an
  unwritable destination cannot strand the next process in a broken state.
- Application logs rotate at a bounded size/count. Diagnostic ZIP export copies only managed log
  files and reapplies the central masking policy while streaming every exported line.
- Recent-log views resolve only the configured log directory, accept fixed managed filenames,
  and cap file count, bytes read, field length, and returned rows. Missing, corrupt, or unreadable
  files are skipped without taking down the CLI or GUI.
- Portable executables always write mutable state below the per-user data directory; the extracted
  application directory is treated as read-only.
- S3 files at or above 16 MiB use explicit multipart upload with 8 MiB parts and one in-flight part,
  bounding transfer memory independently of file size. Cancellation is cooperative at part
  boundaries; cancel or any post-create failure attempts `AbortMultipartUpload` before returning.
  An abort failure is appended to the typed diagnostic cause without replacing the original error.
- S3 progress identifies the current final target URI. GUI shutdown waits for the owned upload's
  cancellation result, including multipart abort completion, before closing the application.
- S3 upload MFA cancellation clears both the pending authenticated operation and GUI task handle;
  a later GUI shutdown must therefore complete immediately rather than waiting on a finished task.
- S3 folder upload rejects symbolic links, preserves source-relative paths, and de-duplicates target
  keys before transfer. `SKIP_EXISTING` rechecks each target and transfers only absent keys, matching
  `aws s3 sync --no-overwrite` semantics without requiring an AWS CLI process.
- S3 transfer failures are never retried automatically. An explicit user retry creates a fresh
  operation and multipart upload, resets progress to zero, and reruns exact-key preflight so a
  previous partial success cannot be overwritten without renewed consent.

- S3 download preflight reports existing local files for explicit overwrite/skip/cancel. Default skip rechecks before transfer. Atomic commit refuses replacement unless overwrite was approved for an existing preflight target; newly appearing targets require another confirmation. Direct CLI downloads also refuse implicit replacement.
- Local upload previews run through GuiTaskRunner and use image downsampling (104×72 target, at most 20 MiB input and 16 million pixels). Unsupported or unreadable images retain type icons; metadata errors are shown on the card. Clearing/rebuilding cards cancels queued previews and ignores stale results.
