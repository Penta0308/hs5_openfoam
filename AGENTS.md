# hs5 Agent Instructions

## Inventor Safety Rule

- Never close, quit, kill, restart, or otherwise terminate Autodesk Inventor from automation.
- Do not attach to `Inventor.Application` through COM unless the user explicitly asks for an Inventor operation in the current turn.
- Python/COM tools must never start Inventor; when explicitly permitted, they may attach only to an already-running `Inventor.Application` instance.
- Do not call `app.Quit()`, `taskkill`, `Stop-Process`, or any equivalent Inventor shutdown command.
- If Inventor is not running, fail with a clear error telling the user to open Inventor manually.
- `hs5_cfd.ipt` is the only Inventor file that CFD preparation tools may open.
- Routine validation must be static only. Do not run Inventor state/export scripts as a verification step unless the user explicitly permits live Inventor access.

This rule exists because Inventor is user-owned interactive state; automation must not destroy that session.
