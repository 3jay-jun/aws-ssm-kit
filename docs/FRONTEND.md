# Frontend Guide

The approved interaction authority is [DESIGN.md](DESIGN.md). The
[HTML mockup](design-docs/aws-connect-ui-mockup.html) is the exact authority for
visual geometry, colors, spacing, icons, and state presentation.

- PySide6 views bind presentation view models; they do not call boto3 or SQLite.
- GUI actions call typed Application Services composed by `bootstrap.py`.
- AWS, database, file, and child-process waits never run on the GUI thread.
- Workers publish shared progress/state contracts and honor cancellation.
- The central GUI error mapper owns dialogs, toasts, field errors, and recovery actions.
- UI tests use fake services; business rules belong in Application tests.
- The canonical visual-comparison viewport is 1424 × 894 with a full-window shell and no
  outer inset; the logo region and navigation share a 220 px width; 1024 × 720 is the no-overlap responsive gate with keyboard-accessible actions.
- Shared GUI style tokens and reusable page/card/button/status helpers are the visual SSOT; feature
  pages must not redefine conflicting colors, spacing, radii, or primary/danger button semantics.
- `tools/render_gui.py` renders every real Qt page with isolated local state and no AWS calls for
  deterministic desktop and responsive visual QA.
- The S3 page performs file selection/drop on the GUI thread but runs preflight, listing, and upload
  through `GuiTaskRunner`; it renders the shared `ProgressEvent` and cancels only through the shared
  `CancellationToken`.
