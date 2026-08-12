Changed:
- Added optional `thumbnail_url` passthrough from the candidate item to the assembled context manifest.
- The field defaults to an empty string and was not added to required-field validation or the missing list.

Verification:
- Ran the requested `PYTHONDONTWRITEBYTECODE=1` AST parse command.
- Result: `syntax ok`
