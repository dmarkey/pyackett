"""Go-style template engine subset for Cardigann YAML definitions.

Supports:
  - Variable interpolation: {{ .Config.username }}, {{ .Keywords }}
  - Conditionals: {{ if .Var }}...{{ else }}...{{ end }} including
    {{ else if ... }} chains and arbitrary nesting
  - Range loops: {{ range .Categories }}{{ . }}{{ end }}
  - Logic functions: and, or, eq, ne, not (with nested parenthesised calls)
  - Built-in functions: re_replace, join

The engine parses the template into a small node tree so that nested blocks
pair correctly (a leftmost-first regex approach mis-pairs else/end across
nesting levels). Logic functions are only evaluated inside {{ }} actions, so
literal text such as "foo and .bar" is left untouched.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

# Matches a whole {{ ... }} action, capturing optional Go whitespace-trim
# markers ({{- / -}}).
_ACTION_RE = re.compile(r"\{\{(-?)\s*(.*?)\s*(-?)\}\}", re.DOTALL)

_LOGIC_FUNCS = {"and", "or", "eq", "ne", "not"}
_NUMERIC_RE = re.compile(r"^-?\d+(?:\.\d+)?$")


# --------------------------------------------------------------------------
# Tokenizer
# --------------------------------------------------------------------------


def _tokenize(template: str) -> list[tuple[str, str]]:
    """Split a template into ('text', str) and ('action', str) tokens.

    Go whitespace trim markers ({{- and -}}) strip adjacent whitespace from
    the neighbouring text token.
    """
    tokens: list[tuple[str, str]] = []
    pos = 0
    for m in _ACTION_RE.finditer(template):
        if m.start() > pos:
            tokens.append(("text", template[pos:m.start()]))
        trim_left = m.group(1) == "-"
        trim_right = m.group(3) == "-"
        if trim_left and tokens and tokens[-1][0] == "text":
            tokens[-1] = ("text", tokens[-1][1].rstrip())
        tokens.append(("action", m.group(2).strip()))
        pos = m.end()
        if trim_right:
            # Consume following whitespace in the source before the next token.
            while pos < len(template) and template[pos].isspace():
                pos += 1
    if pos < len(template):
        tokens.append(("text", template[pos:]))
    return tokens


# --------------------------------------------------------------------------
# Parser -> node tree
#   node := ("text", str)
#         | ("action", str)
#         | ("if", [(cond, [node...]), ...], else_nodes|None)
#         | ("range", pipeline, [node...])
# --------------------------------------------------------------------------


def _parse_nodes(tokens: list[tuple[str, str]], i: int) -> tuple[list, int]:
    """Parse nodes until a terminator action (end/else/else if) or EOF.

    Returns (nodes, index_of_terminator_or_len).
    """
    nodes: list = []
    while i < len(tokens):
        kind, content = tokens[i]
        if kind == "text":
            nodes.append(("text", content))
            i += 1
            continue
        # action
        if content == "end" or content == "else" or content.startswith("else "):
            return nodes, i  # leave terminator for the caller
        if content == "if" or content.startswith("if "):
            node, i = _parse_if(tokens, i)
            nodes.append(node)
        elif content == "range" or content.startswith("range "):
            node, i = _parse_range(tokens, i)
            nodes.append(node)
        else:
            nodes.append(("action", content))
            i += 1
    return nodes, i


def _parse_if(tokens: list[tuple[str, str]], i: int) -> tuple[tuple, int]:
    cond = tokens[i][1][2:].strip()  # strip leading "if"
    i += 1
    branches: list[tuple[str, list]] = []
    else_nodes: list | None = None
    while True:
        body, i = _parse_nodes(tokens, i)
        branches.append((cond, body))
        if i >= len(tokens):
            break  # unterminated; render what we have
        term = tokens[i][1]
        if term == "end":
            i += 1
            break
        if term == "else":
            i += 1
            else_nodes, i = _parse_nodes(tokens, i)
            if i < len(tokens) and tokens[i][1] == "end":
                i += 1
            break
        if term.startswith("else if ") or term.startswith("elseif "):
            cond = term.split("if", 1)[1].strip()
            i += 1
            continue
        # Unexpected terminator (e.g. "else foo") — treat as end of block.
        i += 1
        break
    return ("if", branches, else_nodes), i


def _parse_range(tokens: list[tuple[str, str]], i: int) -> tuple[tuple, int]:
    pipeline = tokens[i][1][5:].strip()  # strip leading "range"
    i += 1
    body, i = _parse_nodes(tokens, i)
    if i < len(tokens) and tokens[i][1] == "end":
        i += 1
    return ("range", pipeline, body), i


# --------------------------------------------------------------------------
# Expression tokenizer / evaluator (conditions and function calls)
# --------------------------------------------------------------------------


def _tokenize_expr(s: str) -> list:
    """Tokenize an expression into ('str', value), ('word', value), '(' and ')'."""
    tokens: list = []
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c.isspace():
            i += 1
            continue
        if c in "()":
            tokens.append(c)
            i += 1
            continue
        if c in "\"'":
            quote = c
            j = i + 1
            while j < n and s[j] != quote:
                j += 1
            tokens.append(("str", s[i + 1:j]))
            i = j + 1
            continue
        j = i
        while j < n and not s[j].isspace() and s[j] not in "()":
            j += 1
        tokens.append(("word", s[i:j]))
        i = j
    return tokens


def _parse_expr(tokens: list, pos: int) -> tuple[list, int]:
    """Parse a sequence of atoms up to ')' or end. Returns (atoms, pos)."""
    atoms: list = []
    while pos < len(tokens) and tokens[pos] != ")":
        atom, pos = _parse_atom(tokens, pos)
        atoms.append(atom)
    return atoms, pos


def _parse_atom(tokens: list, pos: int) -> tuple[Any, int]:
    tok = tokens[pos]
    if tok == "(":
        atoms, pos = _parse_expr(tokens, pos + 1)
        if pos < len(tokens) and tokens[pos] == ")":
            pos += 1
        return ("group", atoms), pos
    return ("tok", tok), pos + 1


def _resolve_var(name: str, variables: dict[str, Any]) -> Any:
    """Resolve a dotted variable name against the variables dict."""
    if name in variables:
        return variables[name]

    no_dot = name.lstrip(".")
    prefixed = "." + no_dot
    if prefixed in variables:
        return variables[prefixed]

    parts = no_dot.split(".")
    current: Any = variables
    for part in parts:
        if isinstance(current, dict):
            if part in current:
                current = current[part]
            elif "." + part in current:
                current = current["." + part]
            else:
                return None
        else:
            return None
    return current


def _is_truthy(value: Any) -> bool:
    """Evaluate truthiness the Go template way (any non-empty string is true)."""
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return len(value) > 0
    if isinstance(value, (list, tuple, dict)):
        return len(value) > 0
    if isinstance(value, (int, float)):
        return value != 0
    return bool(value)


def _eval_single(atom: Any, variables: dict[str, Any]) -> Any:
    """Evaluate a single atom to a Python value."""
    if atom[0] == "group":
        return _eval_atoms(atom[1], variables)
    tok = atom[1]
    if isinstance(tok, tuple) and tok[0] == "str":
        return tok[1]  # string literal
    word = tok[1] if isinstance(tok, tuple) else tok
    if word.startswith("."):
        return _resolve_var(word, variables)
    if word == "true":
        return True
    if word == "false":
        return False
    if _NUMERIC_RE.match(word):
        return float(word) if "." in word else int(word)
    return word  # bare word treated as literal


def _values_equal(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    sa, sb = str(a), str(b)
    if _NUMERIC_RE.match(sa) and _NUMERIC_RE.match(sb):
        return float(sa) == float(sb)
    return sa == sb


def _apply_func(name: str, argvals: list[Any]) -> Any:
    if name == "and":
        result: Any = True
        for v in argvals:
            if not _is_truthy(v):
                return v
            result = v
        return result
    if name == "or":
        result = argvals[-1] if argvals else ""
        for v in argvals:
            if _is_truthy(v):
                return v
        return result
    if name == "not":
        return not _is_truthy(argvals[0]) if argvals else True
    if name in ("eq", "ne"):
        if len(argvals) < 2:
            return False
        equal = _values_equal(argvals[0], argvals[1])
        return equal if name == "eq" else not equal
    return ""


def _eval_atoms(atoms: list, variables: dict[str, Any]) -> Any:
    """Evaluate a parsed atom list (a pipeline) to a value."""
    if not atoms:
        return ""
    first = atoms[0]
    if first[0] == "tok":
        tok = first[1]
        word = tok[1] if isinstance(tok, tuple) and tok[0] == "word" else None
        if word in _LOGIC_FUNCS:
            argvals = [_eval_single(a, variables) for a in atoms[1:]]
            return _apply_func(word, argvals)
    # Not a function call: the value is the first atom (extras ignored).
    return _eval_single(first, variables)


def _eval_condition(cond: str, variables: dict[str, Any]) -> bool:
    tokens = _tokenize_expr(cond)
    atoms, _ = _parse_expr(tokens, 0)
    return _is_truthy(_eval_atoms(atoms, variables))


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def _render_action(
    expr: str, variables: dict[str, Any], modifier: Callable[[str], str] | None
) -> str:
    tokens = _tokenize_expr(expr)
    atoms, _ = _parse_expr(tokens, 0)
    if not atoms:
        return ""

    first = atoms[0]
    head = None
    if first[0] == "tok":
        tok = first[1]
        if isinstance(tok, tuple) and tok[0] == "word":
            head = tok[1]

    if head == "re_replace" and len(atoms) >= 4:
        value = _eval_single(atoms[1], variables)
        pattern = _eval_single(atoms[2], variables)
        replacement = _eval_single(atoms[3], variables)
        # Convert Go/.NET-style $1 backreferences to Python \1.
        replacement = re.sub(r"\$(\d+)", r"\\\1", str(replacement))
        try:
            out = re.sub(str(pattern), replacement, str(value) if value is not None else "")
        except re.error:
            out = str(value) if value is not None else ""
        return modifier(out) if modifier else out

    if head == "join" and len(atoms) >= 3:
        items = _eval_single(atoms[1], variables)
        delimiter = str(_eval_single(atoms[2], variables))
        if isinstance(items, (list, tuple)):
            out = delimiter.join(str(x) for x in items)
        else:
            out = ""
        return modifier(out) if modifier else out

    if head in _LOGIC_FUNCS:
        val = _eval_atoms(atoms, variables)
        if isinstance(val, bool):
            return "true" if val else ""
        out = str(val) if val is not None else ""
        return modifier(out) if modifier else out

    # Plain variable (or dot in range bodies).
    val = _eval_single(first, variables)
    if val is None:
        return ""
    if isinstance(val, bool):
        val = "true" if val else "false"
    out = str(val)
    return modifier(out) if modifier else out


def _render(
    nodes: list, variables: dict[str, Any], modifier: Callable[[str], str] | None
) -> str:
    out: list[str] = []
    for node in nodes:
        kind = node[0]
        if kind == "text":
            out.append(node[1])
        elif kind == "action":
            out.append(_render_action(node[1], variables, modifier))
        elif kind == "if":
            branches, else_nodes = node[1], node[2]
            rendered = None
            for cond, body in branches:
                if _eval_condition(cond, variables):
                    rendered = _render(body, variables, modifier)
                    break
            if rendered is None:
                rendered = _render(else_nodes, variables, modifier) if else_nodes else ""
            out.append(rendered)
        elif kind == "range":
            pipeline, body = node[1], node[2]
            tokens = _tokenize_expr(pipeline)
            atoms, _ = _parse_expr(tokens, 0)
            items = _eval_atoms(atoms, variables)
            if isinstance(items, (list, tuple)):
                for item in items:
                    item_vars = dict(variables)
                    item_vars["."] = item
                    out.append(_render(body, item_vars, modifier))
    return "".join(out)


def apply_template(
    template: str,
    variables: dict[str, Any],
    modifier: Callable[[str], str] | None = None,
) -> str:
    """Apply Go-style template substitution.

    Args:
        template: The template string with {{ }} expressions.
        variables: Dict of variable names to values (keys prefixed with ".").
        modifier: Optional function to post-process expanded values (e.g. URL encoding).

    Returns:
        The expanded template string.
    """
    if not template or "{{" not in template:
        return template or ""

    tokens = _tokenize(template)
    nodes, _ = _parse_nodes(tokens, 0)
    return _render(nodes, variables, modifier)
