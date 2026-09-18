# aws-ssm-kit Architecture Map

This is a map. Detailed decisions and contracts live under `docs/`.

## System

aws-ssm-kit is a Windows-first Python application distributed as a Portable ZIP.
The CLI verifies each use case before the GUI connects to the same Application Service.

```text
presentation/cli ─┐
                  ├─ application ─> domain
presentation/gui ─┘       │
                           └─ ports <─ infrastructure
```

- `src/aws_connect/domain/`: invariants, value objects, and typed errors.
- `src/aws_connect/application/`: use cases, DTOs, operations, and ports.
- `src/aws_connect/infrastructure/`: AWS, SQLite, DPAPI, filesystem, and process adapters.
- `src/aws_connect/presentation/`: CLI and GUI input/output mapping only.
- `src/aws_connect/bootstrap.py`: the only production composition root.

## Non-negotiable Boundaries

- Domain imports no outer layer; Application imports neither Infrastructure nor Presentation.
- CLI and GUI never implement business rules or parse each other's output.
- AWS and Windows exceptions become typed application errors at adapter boundaries.
- Long-running work uses shared progress, cancellation, and handle contracts.
- Secrets never appear in logs, command arguments, fixtures, or default output.

## Detailed Knowledge

- Product: `docs/product-specs/index.md`
- Design: `docs/design-docs/index.md`, `docs/DESIGN.md`, `docs/FRONTEND.md`
- Current work: `docs/exec-plans/active/`
- Security and reliability: `docs/SECURITY.md`, `docs/RELIABILITY.md`
- Standard checks: `docs/PLANS.md` and `./scripts/check.ps1`


## Structured execution history

Application use cases record through `ExecutionLogService` (the compatible `ActivityLogService` API).
`ExecutionLogRepository` is implemented by the existing SQLite store, migration 10.
A shared sanitizer runs before SQLite/file serialization and after database reads.
GUI logs and dashboard recent work query SQLite; rotating Python logs remain diagnostic artifacts.
