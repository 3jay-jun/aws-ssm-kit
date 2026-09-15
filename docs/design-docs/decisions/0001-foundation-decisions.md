# ADR 0001: Foundation Decisions

- Status: Accepted
- Date: 2026-09-09

## Context

Phase 0 requires stable choices for UI, packaging, user data, platform support,
feature phasing, and real AWS verification.

## Decision

- GUI: PySide6.
- Packaging: PyInstaller `onedir`, distributed as a Portable ZIP.
- User data: `%LOCALAPPDATA%\AWSConnect` rather than the executable directory.
- Platform: Windows 10 22H2 or newer and Windows 11.
- Scope: Secrets Manager and S3 follow the EC2/RDS MVP.
- AWS smoke tests: an explicitly approved, dedicated least-privilege account only.

## Consequences

The package is larger but can implement the approved mockup and is easier to
diagnose than one-file extraction. User data remains writable and survives upgrades.
Release testing needs a clean supported Windows VM and controlled AWS resources.

