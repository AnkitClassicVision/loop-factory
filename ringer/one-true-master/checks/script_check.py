#!/usr/bin/env python3
"""Validate an authored, reusable episode-packaging script.

The worker writes the script; the coordinator runs it against the live estate.
So this checks the script is real, reusable, and built on the existing
generators rather than a reinvented one, and it refuses a script that hard-codes
the episode it was written for.

Usage: script_check.py SCRIPT must_import[,..] forbidden_substring[,..]
"""
import ast
import os
import py_compile
import sys
import tempfile


def fail(msg):
    print("FAIL: " + msg)
    sys.exit(1)


def main():
    if len(sys.argv) != 4:
        fail("checker misuse: script_check.py SCRIPT imports forbidden")
    path = sys.argv[1]
    needed = [s.strip() for s in sys.argv[2].split(",") if s.strip()]
    forbidden = [s.strip() for s in sys.argv[3].split(",") if s.strip()]

    if not os.path.isfile(path):
        parent = os.path.dirname(os.path.abspath(path)) or "."
        here = sorted(os.listdir(parent)) if os.path.isdir(parent) else []
        fail("script was never written: %s\nfiles present in %s: %s" % (path, parent, here))

    source = open(path, encoding="utf-8", errors="replace").read()
    if len(source) < 400:
        fail("script is %d chars, which is a stub rather than a working generator.\n"
             "--- content ---\n%s" % (len(source), source))

    # It must actually compile.
    with tempfile.TemporaryDirectory() as tmp:
        try:
            py_compile.compile(path, cfile=os.path.join(tmp, "o.pyc"), doraise=True)
        except py_compile.PyCompileError as exc:
            fail("script does not compile:\n%s" % exc)

    tree = ast.parse(source)

    # It must build on the existing machinery, not reinvent it.
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.add(node.module)
                imported.update("%s.%s" % (node.module, a.name) for a in node.names)
    missing = [n for n in needed if not any(n in i for i in imported)]
    if missing:
        fail("script does not use the existing generator(s) %s.\nit imports: %s\n"
             "Reinventing a renderer or a content builder is explicitly out of scope."
             % (", ".join(missing), ", ".join(sorted(imported)) or "(nothing)"))

    # It must be reusable: no episode baked in.
    hits = [f for f in forbidden if f in source]
    if hits:
        lines = [i + 1 for i, ln in enumerate(source.splitlines())
                 if any(f in ln for f in hits)]
        fail("script hard-codes %s at line(s) %s. It must take the episode directory as an "
             "argument so the next episode can use it unchanged."
             % (", ".join(repr(h) for h in hits), lines[:8]))

    # It must accept an episode directory from outside.
    takes_input = ("argv" in source or "argparse" in source
                   or any(isinstance(n, ast.FunctionDef) and n.args.args for n in ast.walk(tree)))
    if not takes_input:
        fail("script takes no input; it must accept an episode directory as a parameter "
             "or command-line argument.")

    print("PASS: %d chars, compiles, uses %s, no hard-coded episode, accepts an episode "
          "directory." % (len(source), "/".join(needed)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
