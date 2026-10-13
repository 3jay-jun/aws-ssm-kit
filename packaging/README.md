# Windows packaging inputs

`scripts/build-package.ps1` creates the supported PyInstaller `onedir` Portable ZIP.
`packaging/aws_connect.spec` is the only PyInstaller build specification; do not generate or use
a root-level spec because it can silently omit the shared CLI/session-host entry points.
It deliberately does not download or discover a machine-wide Session Manager Plugin.
Release builds require an explicitly reviewed vendor directory containing:

```text
vendor/session-manager-plugin/
|- session-manager-plugin.exe
|- LICENSE
|- NOTICE
`- manifest.json
```

`manifest.json` is an approval record, not information inferred by the build:

```json
{
  "schema_version": 1,
  "artifact": "session-manager-plugin.exe",
  "version": "1.2.707.0",
  "source_url": "https://approved.example/session-manager-plugin.exe",
  "sha256": "64 lowercase hexadecimal characters",
  "signer": "Amazon Web Services, Inc.",
  "license_file": "LICENSE",
  "notice_file": "NOTICE",
  "approved": true,
  "test_only": false
}
```

The checksum must match the exact executable, LICENSE and NOTICE must be non-empty,
the executable must report the declared version, and release inputs must have a valid
Authenticode signature matching the approved signer. A missing, malformed, unapproved,
unsigned, or mismatched input fails closed before PyInstaller starts.

PR CI uses `scripts/create-test-vendor.ps1` to compile a harmless version-reporting stub.
That manifest is marked `test_only`; it is accepted only with `-AllowTestVendor`, produces
a `TEST-ONLY` ZIP, and cannot satisfy the release input checks. No synthetic artifact may
be renamed or published as an AWS Session Manager Plugin distribution.

No custom PyInstaller hook is currently needed: the upstream PyInstaller hooks cover
PySide6, boto3, and botocore. The checked-in spec explicitly gathers botocore data and
dynamic service modules so this assumption is visible and package-smoked in CI.

## Build and verification

A production build intentionally fails unless the reviewed vendor directory above is supplied:

```powershell
./scripts/build-package.ps1 -VendorDirectory ./vendor/session-manager-plugin
./scripts/test-package.ps1 -ZipPath ./dist/aws-connect-<version>-windows-x64.zip
```

For deterministic CI/local packaging tests only, create and explicitly opt into the synthetic
vendor. The resulting filename and manifest contain `TEST-ONLY` and are not release artifacts:

```powershell
./scripts/create-test-vendor.ps1 -OutputDirectory ./build/test-vendor
./scripts/build-package.ps1 -VendorDirectory ./build/test-vendor -AllowTestVendor
./scripts/test-package.ps1 -ZipPath ./dist/aws-connect-<version>-TEST-ONLY-windows-x64.zip -AllowTestVendorPackage
```

The smoke gate validates all manifest/checksum records, runs all three sibling executables from a
Korean/space-containing read-only extraction directory, removes Python/AWS CLI/gossm/Plugin from
`PATH`, isolates `%LOCALAPPDATA%`, persists/reopens shared SQLite log settings, and scans generated
DB/log files for plaintext secret patterns.

Before publishing a real release, acquire the official x64 plugin through the organization's
approved supply-chain process, record its HTTPS source, independently verified SHA-256 and version
in `manifest.json`, and review the matching LICENSE/NOTICE. Then run the production commands above
on a clean supported Windows VM with no Python, AWS CLI, gossm, or machine-wide Plugin installed.
Extract beneath a Korean/space path, make the application folder read/execute-only, run `doctor`,
start GUI/CLI, and register/connect the first approved non-production profile within ten minutes.

## GitHub Actions packages and releases

- Pull requests run `.github/workflows/check.yml`: check/test and the existing
  explicitly marked TEST-ONLY packaging smoke job. These packages are test evidence only.
- Pushes to `main` run `.github/workflows/release.yml`: check/test, production build with
  `./vendor/session-manager-plugin`, package smoke, then upload ZIP/Setup and checksums as
  an Actions artifact. No GitHub Release is created for `main`.
- `v*` tag pushes build candidates; manual VM verification and the protected publication workflow are required for GitHub Release publication.

Download the latest successful `main` build from **Actions > release > latest successful
main push run > Artifacts**. Select
`aws-connect-candidate-<run_id>` (retained for 30 days).
The artifact contains the ZIP, Setup, their `.sha256` sidecars and `release-candidate.json`;
run identifiers affect only the artifact
name, not the filenames produced by `build-package.ps1`. Failed checks or package smoke
prevent upload/publication. Production builds never opt into TEST-ONLY vendor inputs.

The approved `vendor/session-manager-plugin` input is versioned with Git LFS. A `vX.Y.Z` tag
whose version exactly matches `pyproject.toml` triggers `.github/workflows/release.yml`. The
workflow runs the complete check gate and builds ZIP/Setup candidates. Publication requires
the separate protected verification workflow below. It never downloads or substitutes
a Session Manager Plugin; the exact reviewed LFS object in the triggering commit is the only
accepted production input, for both main builds and tagged releases.
## Integrated installer and Portable ZIP

Both formats use the same verified application folder. `rclone.exe` is bundled;
`prerequisites/` contains the pinned, signed WinFsp MSI, metadata and license.

```powershell
./scripts/setup-drive-runtime.ps1
./scripts/setup-winfsp-runtime.ps1
./scripts/setup-installer-tools.ps1
./scripts/build-package.ps1 -VendorDirectory ./vendor/session-manager-plugin
./scripts/build-installer.ps1 -PackageDirectory ./build/package/aws-connect
./scripts/test-package.ps1 -ZipPath ./dist/aws-connect-1.0.2-windows-x64.zip -ExpectedWinFspState ready
./scripts/test-installer.ps1 -SetupPath ./dist/aws-connect-1.0.2-windows-x64-Setup.exe
```

Use the version from VERSION.txt in artifact names. Setup installs for the whole PC,
lets users choose C/D and other folders, creates Start Menu and optional desktop shortcuts,
and offers WinFsp setup. Skipping/failing WinFsp still completes app installation.
There is no automatic elevated GUI launch. ZIP users install WinFsp from the S3 mount
or diagnostics controls. The application remains unelevated; only msiexec uses UAC.
Existing WinFsp replacements require explicit confirmation and closed mounts.
Neither uninstall nor upgrade deletes per-user profiles, DPAPI entropy, logs or VFS cache;
shared WinFsp is retained. Quit from the tray and close user-owned EC2 terminals before
upgrading/uninstalling. ZIP upgrades should extract into a new folder.

Offline runtime commands bypass profile/AWS/DB initialization:

```powershell
aws_connect_cli.exe runtime winfsp inspect --output json
aws_connect_cli.exe runtime winfsp install --output json
# Only after confirming replacement of the shared driver:
aws_connect_cli.exe runtime winfsp install --confirm-replace --output json
```

The original doctor `status`/`ready` contract is retained. `core_ready` reports the base
application; `features.s3_mount` and `s3_drive.winfsp_state/version` report mount readiness.
Missing WinFsp permits other features. Reboot-required installations block mount use until
boot identity changes. No automatic reboot or forced MSI termination is performed. GUI
warns after 15 minutes and continues observing the same operation; CLI waits for its final
result so payload locks are retained. Complete quit is deferred while GUI observes installation.

Inno Setup 6.7.3 is checksum/signature pinned; review its commercial usage guidance at
[Inno Setup](https://jrsoftware.org/isinfo.php). The app/Setup itself is currently unsigned;
the upstream WinFsp MSI signature is verified before execution.

### Candidates and verified publication

`release.yml` creates ZIP/Setup candidates and checksums for main/tag runs. It no longer
publishes on tag push. CI builds Inno tooling and prepares both offline runtimes.
`publish-verified.yml` requires a successful tagged candidate run and manual Windows 10/11
VM evidence. Configure the repository `release` environment with required reviewers before
using it for production. The workflow checks the source run, tag commit, artifact hashes,
and every required VM case, then publishes those exact bytes without rebuilding.
Never approve an environment request before its candidate has passed the VM checklist.

The evidence JSON shape is:

```json
{
  "commit": "candidate commit",
  "reviewer": "verification owner",
  "files": {"candidate.zip": "sha256", "candidate-Setup.exe": "sha256"},
  "systems": {
    "windows10": {"os_build": "actual build", "initial_winfsp": "missing", "cases": {}},
    "windows11": {"os_build": "actual build", "initial_winfsp": "missing", "cases": {}}
  }
}
```

Both `cases` objects must contain `passed` for: offline_install,
zip_core_without_winfsp, uac_cancel, alternate_admin, reboot, upgrade,
active_connection_guard, uninstall_data_preserved, s3_read_connect_disconnect.
Record actual evidence, not placeholders. `test-installer.ps1` by default checks integrity
only. `-ExecuteOnTestVm` performs installation exclusively on an approved disposable VM;
interactive UAC/cancellation, reboot and data-preservation cases still require recorded results.
Clean VM verification is a release prerequisite, not replaced by local builds or mocked tests.
