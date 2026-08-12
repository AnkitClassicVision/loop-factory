# Thumbnail dispatch change

- Added optional `thumbnail_url` validation: when present, it must be a string.
- Added `thumbnail_url` to simulated sink records, defaulting to an empty string.
- Added `--media <thumbnail_url>` to live Zernio commands only for a non-empty string.
- Verification passed:
  `PYTHONDONTWRITEBYTECODE=1 python3 -c "import ast; ast.parse(open('/mnt/d_drive/repos/loop-factory/departments/social/runtime/dispatch.py').read()); print('syntax ok')"`
  Output: `syntax ok`
