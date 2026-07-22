import re


class RawReadDenied(RuntimeError):
    pass


RAW_FIELDS = frozenset(
    {"notes_body", "thread", "transcript", "fathom", "hs_note_body", "raw"}
)
PATIENT_ADJACENT = ("dx", "diagnos", "patient", "rx", "prescription")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE_RE = re.compile(r"\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b")
# A string longer than this looks like pasted prose (a thread / transcript /
# note), not a minimized field value, so it is quarantined rather than returned.
MAX_FIELD_LEN = 240


def _redact(text):
    text = EMAIL_RE.sub("[redacted-email]", text)
    return PHONE_RE.sub("[redacted-phone]", text)


def read(source, record, fields):
    out = {}
    for field in fields:
        if field in RAW_FIELDS:
            raise RawReadDenied(field)
        if any(marker in field.casefold() for marker in PATIENT_ADJACENT):
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
            # GLM P1 #8: list/dict/other values could hide raw PHI; never return
            # them raw. Quarantine, fail closed.
            out[field] = {"_quarantined": True}
    return out
