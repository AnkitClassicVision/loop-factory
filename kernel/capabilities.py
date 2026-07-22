"""Capability and environment boundaries for department processes."""


DANGEROUS_EFFECTS = frozenset(
    {
        "external_send",
        "crm_write",
        "publish",
        "model_call",
        "spend",
        "read_sensitive",
    }
)


class AmbientCredentialError(RuntimeError):
    """Raised when credentials are present in a department environment."""


# ALLOWLIST, not a denylist (GLM P1 #5): "no ambient credentials" cannot be
# satisfied by enumerating bad names — anything not explicitly required to run a
# process is dropped. Only these benign, non-secret vars survive.
_ALLOWED_ENV = frozenset(
    {
        "PATH", "HOME", "USER", "LOGNAME", "SHELL", "PWD", "TMPDIR",
        "LANG", "LANGUAGE", "LC_ALL", "LC_CTYPE", "TERM", "TZ",
        "PYTHONPATH", "PYTHONDONTWRITEBYTECODE", "PYTHONUNBUFFERED",
        "OE_KERNEL_ONLY", "PLACEHOLDER_MODE",
        # XDG_RUNTIME_DIR is a path (/run/user/<uid>), not a secret. Without it
        # `systemctl --user` cannot reach the session bus, so watchdog-style
        # departments sense every unit as unknown under confinement (observed
        # podcast shadow run 2026-07-22: 8 real candidates ballooned to 15).
        # Acknowledged widening: the session bus becomes reachable, so a dept
        # process COULD invoke unit actions — that action class stays governed
        # by the heal playbook allowlist, shadow mode, and the systemd unit
        # sandbox (NoNewPrivileges, ProtectSystem=strict).
        "XDG_RUNTIME_DIR",
    }
)

# Retained only for assert_no_ambient_credentials' best-effort leak naming.
_CRED_SUBSTRINGS = ("token", "secret", "key", "password", "credential", "passwd",
                    "bearer", "session", "cookie", "auth")
_CRED_PREFIXES = ("AWS_", "HUBSPOT_", "GMAIL_", "OPENAI_", "ANTHROPIC_")


def _is_cred(name):
    n = name.casefold()
    return any(s in n for s in _CRED_SUBSTRINGS) or any(
        name.upper().startswith(p) for p in _CRED_PREFIXES
    )


def department_env(base):
    """Return the env a department may run with: an allowlist only, plus the
    kernel markers. Everything not explicitly allowed (credentials or not) is
    dropped, so a credential named in any scheme cannot leak in."""
    clean = {k: v for k, v in base.items() if k in _ALLOWED_ENV}
    clean["OE_KERNEL_ONLY"] = "1"
    clean["PLACEHOLDER_MODE"] = "1"
    return clean


def assert_no_ambient_credentials(env):
    """Fail closed if anything outside the allowlist is present (a stricter check
    than name-matching); names matching known credential shapes are reported."""
    disallowed = [k for k in env if k not in _ALLOWED_ENV]
    if disallowed:
        creds = sorted(k for k in disallowed if _is_cred(k)) or sorted(disallowed)
        raise AmbientCredentialError(
            "non-allowlisted environment present: " + ",".join(creds)
        )
