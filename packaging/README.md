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
  "license_file": "LICENSE",
  "notice_file": "NOTICE",
  "approved": true,
  "test_only": false
}
```

The checksum must match the exact executable, LICENSE and NOTICE must be non-empty,
and the executable must report the declared version. A missing, malformed, unapproved,
or mismatched input fails closed before PyInstaller starts.

CI uses `scripts/create-test-vendor.ps1` to compile a harmless version-reporting stub.
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
