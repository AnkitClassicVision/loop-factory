import re


class RawReadDenied(RuntimeError):
    pass


# Field NAMES that are raw-payload class and always denied outright. Generic
# factory defaults; a department extends them per its charter via `extra_raw`
# (review finding: domain-specific names like CRM-vendor fields belong to the
# department layer, not this kernel). Matched casefolded.
RAW_FIELDS = frozenset(
    {"notes_body", "note_body", "thread", "transcript", "raw", "message_body", "body"}
)
# Substrings that mark a field sensitive-adjacent: value is quarantined, never
# returned. Conservative healthcare-adjacent defaults kept as a safety floor;
# extend per department via `extra_sensitive`.
SENSITIVE_ADJACENT = ("dx", "diagnos", "patient", "rx", "prescription")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE_RE = re.compile(r"\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b")
# A string longer than this looks like pasted prose (a thread / transcript /
# note), not a minimized field value, so it is quarantined rather than returned.
MAX_FIELD_LEN = 240


def _redact(text):
    text = EMAIL_RE.sub("[redacted-email]", text)
    return PHONE_RE.sub("[redacted-phone]", text)


def read(source, record, fields, extra_raw=frozenset(), extra_sensitive=()):
    raw_fields = RAW_FIELDS | {f.casefold() for f in extra_raw}
    sensitive = tuple(SENSITIVE_ADJACENT) + tuple(m.casefold() for m in extra_sensitive)
    out = {}
    for field in fields:
        # casefolded: "Transcript"/"RAW" must not bypass the deny list
        if field.casefold() in raw_fields:
            raise RawReadDenied(field)
        if any(marker in field.casefold() for marker in sensitive):
            out[field] = {"_quarantined": True}
            continue
        value = record.get(field)
        if value is None or isinstance(value, (int, float, bool)):
            out[field] = value
        elif isinstance(value, str):
            # GLM P1 #9: a long free-text value may be a pasted thread/transcript
            # under a benign name; quarantine rather than lightly redact.
            out[field] = {"_quarantined": True} if len(value) > MAX_FIELD_LEN else _redact(value)
        else:
            # GLM P1 #8: list/dict/other values could hide raw sensitive data;
            # never return them raw. Quarantine, fail closed.
            out[field] = {"_quarantined": True}
    return out
