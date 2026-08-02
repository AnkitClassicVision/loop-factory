"""Graph schema v2: the typed, executable form of a department's control graph.

v1 `subgraphs.json` is a flat lint-only manifest (factory/graphs.py). v2 keeps
that file and that lint UNTOUCHED and adds, per subgraph, what a deterministic
runner needs to execute it:

  schema_version   2 at the top level (absent/1 = v1: lint-only, not runnable)
  nodes[]          impl, runtime_mode (script|llm), input/output contracts
                   (JSON-schema-lite), receipt_schema, action_class,
                   failure_policy (max_retries, backoff_s, on_fail),
                   concept_ref + interview_ref (traceability kept)
  edges[]          from/to, kind (normal|refusal|escalation|terminal), and a
                   deterministic predicate `when` evaluated over the
                   PREDECESSOR'S RECEIPT JSON

The predicate language is a closed, safe expression subset — comparisons and
boolean combinators over `receipt.*` paths and literals. No eval(), no names,
no calls, no arithmetic, no subscripts. A predicate that cannot be evaluated
(missing field, ordering across types) raises PredicateError: deny-by-default,
the transition BLOCKS — it never silently evaluates to a boolean.
"""
from __future__ import annotations

import math
import re

GRAPH_SCHEMA_VERSION = 2

# Exhaustion caps (documented contract): a predicate is at most
# MAX_PREDICATE_LENGTH characters and MAX_PREDICATE_DEPTH nested
# parentheses/negations. Past either limit it is malformed — PredicateError,
# blocked transition — never a RecursionError that could wedge a run.
MAX_PREDICATE_LENGTH = 4096
MAX_PREDICATE_DEPTH = 32


# --------------------------------------------------------------------------- #
# Predicate subset (deterministic, no eval)
# --------------------------------------------------------------------------- #

class PredicateError(ValueError):
    """The predicate is malformed or cannot be evaluated over this receipt."""


_TOKEN_RE = re.compile(
    r"\s*(?:"
    r"(?P<op>==|!=|<=|>=|<|>)"
    r"|(?P<lparen>\()"
    r"|(?P<rparen>\))"
    r"|(?P<number>-?\d+(?:\.\d+)?)"
    r"|(?P<string>'[^']*'|\"[^\"]*\")"
    r"|(?P<word>[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)"
    r")")

_KEYWORDS = {"and", "or", "not", "true", "false", "null"}


def _tokenize(text: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    pos = 0
    while pos < len(text):
        match = _TOKEN_RE.match(text, pos)
        if match is None or match.end() == pos:
            remainder = text[pos:].strip()
            if not remainder:
                break
            raise PredicateError(f"unexpected input at: {remainder[:20]!r}")
        pos = match.end()
        kind = match.lastgroup
        tokens.append((kind, match.group(kind)))
    return tokens


class _Missing:
    pass


_MISSING = _Missing()


def _family(value) -> str:
    """The type family a JSON value belongs to for comparison purposes.
    bool is deliberately NOT a number: 1 == true must block, not allow."""
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if value is None:
        return "null"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    return type(value).__name__


def _resolve_path(path: str, receipt) -> object:
    segments = path.split(".")
    if segments[0] != "receipt" or len(segments) < 2:
        raise PredicateError(f"paths must be rooted at receipt.*: {path!r}")
    value = receipt
    for segment in segments[1:]:
        if segment.startswith("__"):
            raise PredicateError(f"illegal path segment: {segment!r}")
        if not isinstance(value, dict) or segment not in value:
            raise PredicateError(f"receipt field missing: {path!r}")
        value = value[segment]
    return value


class _Parser:
    """Recursive-descent parser evaluating as it parses. Grammar:

    expr := and_expr ('or' and_expr)*
    and_expr := not_expr ('and' not_expr)*
    not_expr := 'not' not_expr | comparison
    comparison := operand (op operand)?
    operand := literal | receipt-path | '(' expr ')'

    With validate_only=True, receipt paths resolve to a sentinel and any
    expression touching one is treated as boolean — a syntax/shape check that
    never claims runtime semantics.
    """

    def __init__(self, tokens, receipt, validate_only=False):
        self._tokens = tokens
        self._receipt = receipt
        self._validate_only = validate_only
        self._idx = 0
        self._depth = 0

    def _descend(self):
        self._depth += 1
        if self._depth > MAX_PREDICATE_DEPTH:
            raise PredicateError(
                f"predicate nesting depth exceeds {MAX_PREDICATE_DEPTH}")

    def _peek(self):
        if self._idx < len(self._tokens):
            return self._tokens[self._idx]
        return (None, None)

    def _next(self):
        token = self._peek()
        self._idx += 1
        return token

    def parse(self) -> bool:
        result = self._expr()
        if self._idx != len(self._tokens):
            raise PredicateError(f"trailing input after expression: "
                                 f"{self._tokens[self._idx][1]!r}")
        if result is _MISSING and self._validate_only:
            return True
        if not isinstance(result, bool):
            raise PredicateError("predicate must evaluate to a boolean")
        return result

    def _expr(self):
        value = self._and_expr()
        while self._peek() == ("word", "or"):
            self._next()
            right = self._and_expr()
            value = self._as_bool(value) or self._as_bool(right)
        return value

    def _and_expr(self):
        value = self._not_expr()
        while self._peek() == ("word", "and"):
            self._next()
            right = self._not_expr()
            value = self._as_bool(value) and self._as_bool(right)
        return value

    def _not_expr(self):
        if self._peek() == ("word", "not"):
            self._next()
            self._descend()
            try:
                return not self._as_bool(self._not_expr())
            finally:
                self._depth -= 1
        return self._comparison()

    def _comparison(self):
        left = self._operand()
        kind, text = self._peek()
        if kind != "op":
            return left
        self._next()
        right = self._operand()
        return self._compare(text, left, right)

    def _operand(self):
        kind, text = self._next()
        if kind == "lparen":
            self._descend()
            try:
                value = self._expr()
            finally:
                self._depth -= 1
            if self._next()[0] != "rparen":
                raise PredicateError("unbalanced parenthesis")
            return value
        if kind == "number":
            return float(text) if "." in text else int(text)
        if kind == "string":
            return text[1:-1]
        if kind == "word":
            if text == "true":
                return True
            if text == "false":
                return False
            if text == "null":
                return None
            if text in _KEYWORDS:
                raise PredicateError(f"misplaced keyword: {text!r}")
            if self._validate_only:
                if text.split(".")[0] != "receipt" or "." not in text:
                    raise PredicateError(f"paths must be rooted at receipt.*: {text!r}")
                if any(seg.startswith("__") for seg in text.split(".")):
                    raise PredicateError(f"illegal path segment in {text!r}")
                return _MISSING
            return _resolve_path(text, self._receipt)
        raise PredicateError("expected a literal, receipt path, or '('")

    def _as_bool(self, value):
        if value is _MISSING and self._validate_only:
            return True
        if not isinstance(value, bool):
            raise PredicateError("boolean operator applied to non-boolean value")
        return value

    def _compare(self, op, left, right):
        if self._validate_only and (left is _MISSING or right is _MISSING):
            return True
        # Every comparison is defined only WITHIN one type family — Python's
        # == would coerce (1 == True) into a default-allow, so a cross-family
        # comparison of any kind raises and BLOCKS. bool is not a number here.
        left_family, right_family = _family(left), _family(right)
        if left_family != right_family:
            raise PredicateError(
                f"comparison across type families: {left_family} {op} "
                f"{right_family}")
        if op == "==":
            return left == right
        if op == "!=":
            return left != right
        if left_family not in ("number", "string"):
            raise PredicateError(
                f"ordering comparison not defined for {left_family} values")
        if op == "<":
            return left < right
        if op == "<=":
            return left <= right
        if op == ">":
            return left > right
        return left >= right


def eval_predicate(expression: str, receipt: dict) -> bool:
    """Evaluate one edge predicate over the predecessor's receipt JSON.

    Raises PredicateError on any malformed expression or unevaluable reference
    — callers must treat that as a BLOCKED transition, never as False/True.
    """
    if not isinstance(expression, str) or not expression.strip():
        raise PredicateError("empty predicate")
    if len(expression) > MAX_PREDICATE_LENGTH:
        raise PredicateError(
            f"predicate length {len(expression)} exceeds "
            f"{MAX_PREDICATE_LENGTH} (documented length cap)")
    tokens = _tokenize(expression)
    if not tokens:
        raise PredicateError("empty predicate")
    try:
        return _Parser(tokens, receipt).parse()
    except RecursionError as exc:  # defense-in-depth behind the depth cap
        raise PredicateError("predicate exhausted the parser") from exc


def check_predicate(expression: str) -> str | None:
    """Static syntax/shape check for authoring time. Returns the failure text
    or None. Never a substitute for runtime evaluation."""
    try:
        if not isinstance(expression, str) or not expression.strip():
            raise PredicateError("empty predicate")
        if len(expression) > MAX_PREDICATE_LENGTH:
            raise PredicateError(
                f"predicate length {len(expression)} exceeds "
                f"{MAX_PREDICATE_LENGTH} (documented length cap)")
        tokens = _tokenize(expression)
        if not tokens:
            raise PredicateError("empty predicate")
        try:
            _Parser(tokens, receipt=None, validate_only=True).parse()
        except RecursionError as exc:
            raise PredicateError("predicate exhausted the parser") from exc
        return None
    except PredicateError as exc:
        return str(exc)


# --------------------------------------------------------------------------- #
# JSON-schema-lite: the closed contract vocabulary
# --------------------------------------------------------------------------- #

_SCHEMA_KEYWORDS = {"type", "required", "properties", "items", "enum"}
_SCHEMA_TYPES = {"object", "array", "string", "number", "integer", "boolean", "null"}


def check_contract_schema(schema, where="contract") -> list[str]:
    """Validate a contract DECLARATION. Unknown keywords fail closed — a
    constraint the validator would silently ignore is a forged guarantee."""
    fails: list[str] = []
    if not isinstance(schema, dict):
        return [f"{where}: schema must be an object"]
    for key in schema:
        if key not in _SCHEMA_KEYWORDS:
            fails.append(f"{where}: unknown schema keyword '{key}' "
                         f"(allowed: {sorted(_SCHEMA_KEYWORDS)})")
    stype = schema.get("type")
    if stype not in _SCHEMA_TYPES:
        fails.append(f"{where}: type must be one of {sorted(_SCHEMA_TYPES)}")
    required = schema.get("required", [])
    if not (isinstance(required, list) and all(isinstance(r, str) for r in required)):
        fails.append(f"{where}: required must be a list of strings")
    props = schema.get("properties", {})
    if not isinstance(props, dict):
        fails.append(f"{where}: properties must be an object")
    else:
        for name, sub in props.items():
            fails.extend(check_contract_schema(sub, f"{where}.{name}"))
    if "items" in schema:
        fails.extend(check_contract_schema(schema["items"], f"{where}[]"))
    enum = schema.get("enum")
    if enum is not None and not (
            isinstance(enum, list) and enum
            and all(isinstance(v, (str, int, float, bool)) or v is None for v in enum)):
        fails.append(f"{where}: enum must be a non-empty list of JSON scalars")
    return fails


def _type_ok(expected: str, value) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "null":
        return value is None
    return False


def validate_instance(schema: dict, value, where="$") -> list[str]:
    """Validate a VALUE against a contract. Returns failure strings.
    Canonical-JSON policy: non-finite numbers (NaN/Inf) are rejected at every
    contract boundary — they have no canonical JSON form to hash or sign."""
    fails: list[str] = []
    if isinstance(value, float) and not math.isfinite(value):
        fails.append(f"{where}: non-finite number rejected "
                     f"(canonical JSON has no NaN/Inf)")
        return fails
    stype = schema.get("type")
    if stype is not None and not _type_ok(stype, value):
        fails.append(f"{where}: expected {stype}, got {type(value).__name__}")
        return fails
    enum = schema.get("enum")
    if enum is not None and value not in enum:
        fails.append(f"{where}: value {value!r} not in enum {enum}")
    if isinstance(value, dict):
        for name in schema.get("required", []):
            if name not in value:
                fails.append(f"{where}: missing required field '{name}'")
        for name, sub in schema.get("properties", {}).items():
            if name in value:
                fails.extend(validate_instance(sub, value[name], f"{where}.{name}"))
    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            fails.extend(validate_instance(schema["items"], item, f"{where}[{i}]"))
    return fails


# --------------------------------------------------------------------------- #
# v2 manifest validation + reachability
# --------------------------------------------------------------------------- #

EDGE_KINDS = ("normal", "refusal", "escalation", "terminal")
RUNTIME_MODES = ("script", "llm")
_NODE_CONTRACT_FIELDS = ("inputs", "outputs", "receipt_schema")


def manifest_version(data: dict) -> int:
    version = data.get("schema_version", 1)
    if not isinstance(version, int):
        raise ValueError(f"schema_version must be an integer, got {version!r}")
    return version


def executable_nodes(subgraph: dict) -> dict[str, dict]:
    """The nodes the runner executes: everything declaring an impl. Guard
    marker nodes (v1 lint vocabulary) carry no impl and stay metadata —
    their enforcement lives in the kernel gateways, not the runner."""
    return {n["id"]: n for n in subgraph.get("nodes", [])
            if isinstance(n, dict) and n.get("impl") and n.get("id")}


def _validate_node(sid: str, node: dict, edges: list[dict]) -> list[str]:
    fails: list[str] = []
    nid = node.get("id", "?")
    where = f"{sid}/{nid}"
    if node.get("runtime_mode") not in RUNTIME_MODES:
        fails.append(f"{where}: runtime_mode must be one of {list(RUNTIME_MODES)}")
    action_class = node.get("action_class")
    if not (isinstance(action_class, str) and action_class.strip()):
        fails.append(f"{where}: action_class must be a non-empty string")
    for field in _NODE_CONTRACT_FIELDS:
        contract = node.get(field)
        if contract is None:
            fails.append(f"{where}: missing {field} contract")
        else:
            fails.extend(check_contract_schema(contract, f"{where}.{field}"))
    for ref in ("concept_ref", "interview_ref"):
        if not (isinstance(node.get(ref), str) and node[ref].strip()):
            fails.append(f"{where}: missing {ref} (traceability is mandatory)")
    policy = node.get("failure_policy")
    if not isinstance(policy, dict):
        fails.append(f"{where}: missing failure_policy")
        return fails
    retries = policy.get("max_retries")
    if not (isinstance(retries, int) and not isinstance(retries, bool) and retries >= 0):
        fails.append(f"{where}: failure_policy.max_retries must be an integer >= 0")
    backoff = policy.get("backoff_s")
    if not (isinstance(backoff, (int, float)) and not isinstance(backoff, bool)
            and backoff >= 0):
        fails.append(f"{where}: failure_policy.backoff_s must be a number >= 0")
    on_fail = policy.get("on_fail")
    if not (isinstance(on_fail, str) and on_fail.strip()):
        fails.append(f"{where}: failure_policy.on_fail must be 'fail', 'escalate', "
                     f"or a node id")
    elif on_fail not in ("fail", "escalate"):
        matching = [e for e in edges
                    if e.get("from") == nid and e.get("to") == on_fail
                    and e.get("kind") in ("refusal", "escalation")]
        if not matching:
            fails.append(
                f"{where}: on_fail target '{on_fail}' has no refusal/escalation "
                f"edge from {nid} — the failure route must be a declared edge")
    return fails


def _validate_edges(sid: str, edges: list[dict], node_ids: set[str]) -> list[str]:
    fails: list[str] = []
    for i, edge in enumerate(edges):
        where = f"{sid}/edge#{i}"
        if not isinstance(edge, dict):
            fails.append(f"{where}: edge must be an object")
            continue
        kind = edge.get("kind")
        if kind not in EDGE_KINDS:
            fails.append(f"{where}: kind must be one of {list(EDGE_KINDS)}")
        src = edge.get("from")
        if src not in node_ids:
            fails.append(f"{where}: from references unknown node '{src}'")
        if kind == "terminal":
            if "to" in edge:
                fails.append(f"{where}: terminal edges end the run and take no 'to'")
        elif kind == "escalation" and "to" not in edge:
            pass  # target-less escalation routes to the manager/outbox plane
        else:
            if edge.get("to") not in node_ids:
                fails.append(f"{where}: to references unknown node '{edge.get('to')}'")
        problem = check_predicate(edge.get("when"))
        if problem is not None:
            fails.append(f"{where}: bad predicate: {problem}")
    return fails


def _validate_reachability(sid: str, entry: str, node_ids: set[str],
                           edges: list[dict]) -> list[str]:
    fails: list[str] = []
    forward: dict[str, set[str]] = {n: set() for n in node_ids}
    backward: dict[str, set[str]] = {n: set() for n in node_ids}
    terminal_sources: set[str] = set()
    for edge in edges:
        src, dst, kind = edge.get("from"), edge.get("to"), edge.get("kind")
        if kind == "terminal" and src in node_ids:
            terminal_sources.add(src)
        elif src in node_ids and dst in node_ids:
            forward[src].add(dst)
            backward[dst].add(src)

    def _closure(seeds: set[str], adjacency: dict[str, set[str]]) -> set[str]:
        seen: set[str] = set()
        frontier = [s for s in seeds if s in node_ids]
        while frontier:
            current = frontier.pop()
            if current in seen:
                continue
            seen.add(current)
            frontier.extend(adjacency[current] - seen)
        return seen

    reachable = _closure({entry}, forward)
    for nid in sorted(node_ids - reachable):
        fails.append(f"{sid}: node {nid} is unreachable from entry '{entry}'")
    if not terminal_sources:
        fails.append(f"{sid}: no terminal edge — every run would be unfinishable")
    can_finish = _closure(terminal_sources, backward)
    for nid in sorted(node_ids - can_finish):
        fails.append(f"{sid}: node {nid} has no path to a terminal edge")
    return fails


def validate_subgraph_v2(subgraph: dict) -> list[str]:
    """Full v2 validation for one subgraph. A subgraph with neither 'entry'
    nor 'edges' is a v1 lint-only subgraph and is skipped (incremental
    adoption: the runner is optional per department and per subgraph)."""
    sid = subgraph.get("id", "?")
    has_v2 = "entry" in subgraph or "edges" in subgraph
    if not has_v2:
        return []
    fails: list[str] = []
    nodes = executable_nodes(subgraph)
    ids = [n.get("id") for n in subgraph.get("nodes", []) if isinstance(n, dict)]
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        fails.append(f"{sid}: duplicate node id '{dup}'")
    if not nodes:
        fails.append(f"{sid}: v2 subgraph has no executable (impl-bearing) nodes")
        return fails
    edges = subgraph.get("edges")
    if not isinstance(edges, list) or not edges:
        fails.append(f"{sid}: v2 subgraph must declare edges[]")
        edges = []
    entry = subgraph.get("entry")
    if entry not in nodes:
        fails.append(f"{sid}: entry must name an executable node, got {entry!r}")
    node_ids = set(nodes)
    for node in nodes.values():
        fails.extend(_validate_node(sid, node, edges))
    fails.extend(_validate_edges(sid, edges, node_ids))
    if entry in nodes and edges:
        fails.extend(_validate_reachability(sid, entry, node_ids, edges))
    return fails


def validate_manifest(data: dict) -> list[str]:
    """v2 additions only. The v1 guard-matrix lint (factory/graphs.py) stays
    the authority on guard ordering and runs unchanged next to this."""
    try:
        version = manifest_version(data)
    except ValueError as exc:
        return [str(exc)]
    if version == 1:
        return []
    if version != GRAPH_SCHEMA_VERSION:
        return [f"unsupported schema_version {version} "
                f"(this factory speaks 1 and {GRAPH_SCHEMA_VERSION})"]
    fails: list[str] = []
    for subgraph in data.get("subgraphs", []):
        if isinstance(subgraph, dict):
            fails.extend(validate_subgraph_v2(subgraph))
    return fails
