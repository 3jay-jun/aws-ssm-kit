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
  outer inset or global top header; the logo, profile selector, and navigation share a 220 px
  sidebar; 1024 × 720 is the no-overlap responsive gate with keyboard-accessible actions.
- The sidebar profile selector is the only global authentication control. It displays only the
  profile name, authentication state, and remaining token time; account/user details stay in the
  profile dialog. S3 file/mount routes share one accordion and activity history stays at the bottom.
- Shared GUI style tokens and reusable page/card/button/status helpers are the visual SSOT; feature
  pages must not redefine conflicting colors, spacing, radii, or primary/danger button semantics.
- RDS duration choices come from the tunnel domain, not the profile authentication constants.
  The existing 2-second worker poll observes all owned profiles; only the displayed rows are
  filtered by current profile. Poll requests cannot overlap. Expiration is a typed terminal
  reason rendered through the existing notice signal, never an error dialog on clean cleanup.
- `tools/render_gui.py` renders every real Qt page with isolated local state and no AWS calls for
  deterministic desktop and responsive visual QA.
- The S3 page performs file selection/drop on the GUI thread but runs preflight, listing, and upload
  through `GuiTaskRunner`; it renders the shared `ProgressEvent` and cancels only through the shared
  `CancellationToken`.

- Every tab uses `apply_page_layout` and `page_heading` from `presentation/gui/page_layout.py`.
  Insets are 24px horizontal / 20px vertical, section spacing 16px, heading gap 4px.
  Typography sizes are defined in `typography.py`: page title 25px, section title 17px, body 13px.
- Checkbox indicators (widgets, item views, and menus) share the global MFA-derived QSS rule.
  Do not override checkbox size/color or page-title font size inside a feature selector.
