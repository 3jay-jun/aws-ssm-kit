# Security Rules

- Protect long-lived credentials and session tokens with Windows DPAPI.
- Protect each application data/log directory with a non-inherited Windows DACL containing one
  inheritable full-control ACE for the current user. A SQLite database/sidecar or managed log
  created later may carry that ACE as inherited, but its effective DACL must still contain no
  other trustee; existing DB/sidecar/log files, DPAPI entropy, and diagnostic archives are
  explicitly restricted to the same current-user-only access. Treat every ACL application failure
  as a typed, user-visible application failure; never continue with a broader effective DACL.
- Never hardcode or commit Access Keys, Secret Keys, Session Tokens, MFA codes, Secret values, or real customer resource IDs.
- Never expose secrets in AWS Connect launcher/helper arguments, logs, fixtures, snapshots,
  tracebacks, or default CLI/GUI output. The upstream Session Manager Plugin requires
  `StreamUrl` and `TokenValue` in its final invocation contract; keep that unavoidable,
  short-lived exposure at the Plugin boundary only and never serialize or log the raw argv.
- Transfer the Plugin invocation to the external terminal helper only through an in-memory
  named pipe. Authenticate each launch with an independent 256-bit key; put only its
  current-user DPAPI ciphertext in the helper command line and create no key file.
- Apply one central masking policy before serialization and logging.
- Validate identity against the configured Account and IAM user before reusing a token.
- Keep an MFA-deferred feature callable only in bounded process memory with its representation
  suppressed. Never serialize, persist, or log the callable, its captured request, or MFA code;
  cancellation, expiry, invalid input, capacity rejection, and the single resume all remove the
  corresponding pending state.
- Use least-privilege, non-production profiles for explicitly approved AWS smoke tests.
- Do not perform IAM changes, live S3 deletion, RDS data modification, or other destructive AWS operations in automated tests. Stubber may verify an exact-key `DeleteObject` request without contacting AWS.
- Credential deletion and overwrite require an explicit user action and clear target identification.
- Legacy INI import is two-step: preview exposes metadata only and apply requires explicit
  confirmation. The source file is read-only input and is never rewritten or deleted; imported
  credentials cross directly into DPAPI-protected persistence and are not returned in result DTOs.
- Diagnostic export reapplies central masking even when a historical log predates the current
  masking rule. Export destinations inside the live log directory are rejected.
- Activity records use a fixed metadata schema (`feature`, `target`, `result`, message code,
  correlation/operation ID). Command arguments, credentials, MFA input, Secret/file contents,
  `StreamUrl`, and `TokenValue` are not accepted by that schema; managed-log reads reapply the
  central masking policy before values reach either presentation adapter.
- Secrets Manager values remain in memory only in a `repr=False` operation result. Default CLI
  and GUI views mask sensitive names and all plain-string/scalar values; raw values cross the
  presentation boundary only after an explicit CLI reveal/update, timed GUI reveal, or RDS
  endpoint-copy action. GUI reveal is cleared after 30 seconds, and a new lookup or profile change
  discards the previous result.
- SQLite `saved_secrets` stores only profile-scoped Secret names or ARNs. Successful lookup may
  auto-register that identifier, but SecretString, flattened fields, revealed values and versions
  never enter this table. The GUI has no Secret value copy or PutSecretValue action.
- RDS local endpoint copy exposes only `127.0.0.1:<local-port>` and uses the same conditional
  30-second clipboard clearing policy; it never copies the RDS host or credentials implicitly.
- RDS endpoint copy reads only top-level `host` and `port`, populates an unsaved editor, and never
  persists any other Secret field. Neither lookup results nor clipboard values enter SQLite or logs.
- A production package requires an approved, checksummed upstream Session Manager Plugin plus its
  LICENSE and NOTICE. Synthetic plugin inputs remain visibly `TEST-ONLY` and cannot satisfy the
  release build gate.
- S3 never requires `ListBuckets`; an optional catalog request may populate the Bucket selector and
  permission failure must fall back to direct Bucket/Prefix input. Object browsing and exact-key
  overwrite preflight use `ListObjectsV2`. Upload is preview-only until explicit confirmation, and an existing key also
  requires explicit overwrite consent. Local file contents are streamed in memory and are never
  persisted to SQLite, logs, diagnostics, or default CLI/GUI output. Single-object deletion requires
  an exact selected key and confirmation; prefix recursion and batch deletion are not implemented.
  A user-requested download writes to a temporary file beside the chosen destination and atomically
  replaces it only after GetObject succeeds; failed partial files are removed before reporting error.
- EC2 start and reboot are explicit, confirmed operations scoped to one selected instance. The app
  never exposes StopInstances or TerminateInstances, and records only masked structured metadata.
- EC2-mediated Secret lookup is an explicit opt-in path, never an automatic permission fallback.
  It targets one Online managed node with a fixed command template and validated Secret identifier,
  disables S3/CloudWatch command output, and keeps returned plaintext memory-only. The UI warns that
  Systems Manager command output can temporarily contain the Secret value; logs, SQLite, diagnostics,
  repr, command arguments shown by AWS Connect, and clipboard history must not retain it.
- When `ssm:SendCommand` is denied, the user may explicitly open an
  `AWS-StartInteractiveCommand` session. The app passes only the same fixed, validated lookup and a
  platform-specific shell continuation so the terminal remains open after the value is printed.
  Plaintext stays in the external terminal and is never captured by the app; arbitrary commands and
  unvalidated Secret identifiers remain prohibited.
- Detailed activity records accept only bounded structured error fields. Technical text is centrally
  masked before write, re-masked after read, and re-masked before copy; arbitrary tracebacks, command
  arguments and response payloads are excluded from the schema.
