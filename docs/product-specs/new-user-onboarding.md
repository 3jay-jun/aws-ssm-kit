# New User Onboarding

## Desired outcome

A Windows user extracts the Portable ZIP, starts aws-ssm-kit, stores a protected
AWS profile, completes MFA when required, and can diagnose prerequisites without
installing Python, AWS CLI, or gossm.

Users may instead run Setup, choose an installation folder and create Start Menu/desktop
shortcuts. Both distributions include rclone and an offline WinFsp installer. Setup offers
WinFsp installation; ZIP users can install it from S3 mount or diagnostics. Cancellation or
failure leaves the base application usable and explains why S3 mount is unavailable.
Replacing/repairing an existing shared WinFsp requires confirmation. Uninstall preserves
user profiles, logs, VFS cache and shared WinFsp. Clean Windows verification remains a release
requirement; local candidates are not evidence of clean-machine installation.

## MVP flow

1. Launch `aws_connect.exe`.
2. Create or select an AWS profile.
3. Validate identity and reuse or refresh the session token.
4. Use EC2 connection or a saved RDS tunnel session.
5. Use `aws_connect_cli.exe doctor` when local prerequisites fail.

Detailed acceptance criteria live in [the product requirements](aws-connect.md).

