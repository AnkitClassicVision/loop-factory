#!/usr/bin/env python3
"""Validate an independent audit report.

The point of the audit is to check someone else's claims by re-running them, so
this checker refuses a report that merely agrees. Every claim must carry a
verdict AND the command that produced it, and a claim marked PASS with no
command is treated as an unsupported assertion rather than a verification.

Usage: audit_check.py REPORT N_CLAIMS
"""
import os
import re
import sys

VERDICTS = ("PASS", "FAIL", "UNVERIFIABLE")


def fail(msg):
    print("FAIL: " + msg)
    sys.exit(1)


def main():
    if len(sys.argv) != 3:
        fail("checker misuse: audit_check.py REPORT N_CLAIMS")
    path, n = sys.argv[1], int(sys.argv[2])

    if not os.path.isfile(path):
        parent = os.path.dirname(os.path.abspath(path)) or "."
        here = sorted(os.listdir(parent)) if os.path.isdir(parent) else []
        fail("report was never written: %s\nfiles present: %s" % (path, here))

    text = open(path, encoding="utf-8", errors="replace").read()
    if len(text) < 1500:
        fail("report is %d chars; an audit of %d claims cannot be that short.\n"
             "--- report ---\n%s" % (len(text), n, text[:600]))

    missing, bad, unsupported = [], [], []
    for i in range(1, n + 1):
        cid = "CLAIM-%02d" % i
        m = re.search(cid + r"\s*[:\-]\s*(\w+)", text)
        if not m:
            missing.append(cid)
            continue
        verdict = m.group(1).upper()
        if verdict not in VERDICTS:
            bad.append("%s has verdict %r, must be one of %s"
                       % (cid, m.group(1), "/".join(VERDICTS)))
            continue
        # The evidence for a claim is whatever follows it up to the next claim.
        start = m.end()
        nxt = re.search(r"CLAIM-\d\d", text[start:])
        body = text[start:start + (nxt.start() if nxt else len(text))]
        if not re.search(r"[$>`]|\bpytest\b|\bgit\b|\bgrep\b|\bpython3\b|\bffprobe\b",
                         body):
            unsupported.append(cid)

    if missing:
        fail("the report never reaches these claims: %s\n"
             "Every claim must appear as 'CLAIM-NN: VERDICT'." % ", ".join(missing))
    if bad:
        fail("invalid verdicts:\n  " + "\n  ".join(bad))
    if unsupported:
        fail("these claims carry a verdict but show no command that produced it: %s\n"
             "An audit that asserts without executing is not an audit. Include the "
             "exact command you ran for each." % ", ".join(unsupported))

    counts = {v: len(re.findall(r"CLAIM-\d\d\s*[:\-]\s*" + v, text, re.I))
              for v in VERDICTS}
    print("PASS: %d claims, all with a verdict and a command. %s"
          % (n, ", ".join("%s=%d" % (k, v) for k, v in counts.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
