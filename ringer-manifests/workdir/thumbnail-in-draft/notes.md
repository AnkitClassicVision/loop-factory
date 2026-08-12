# Thumbnail URL draft pass-through

- Changed `departments/social/runtime/draft_post.py` so `_normalize_draft()` copies `bundle["thumbnail_url"]` into the normalized draft, defaulting to an empty string.
- Did not add `thumbnail_url` to the engine prompt or draft validation.
- Verification passed: the requested AST parse command printed `syntax ok` and exited 0.
