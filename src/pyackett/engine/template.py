"""Go-style template engine subset for Cardigann YAML definitions.

Supports:
  - Variable interpolation: {{ .Config.username }}, {{ .Keywords }}
  - Conditionals: {{ if .Var }}...{{ else }}...{{ end }}
  - Range loops: {{ range .Categories }}{{ . }}{{ end }}
  - Logic functions: and, or, eq, ne
  - Built-in functions: re_replace, join
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

# Matches {{ range .Variable }}...{{ end }}
_RANGE_RE = re.compile(
    r"\{\{\s*range\s+(\.\w+(?:\.\w+)*)\s*\}\}(.*?)\{\{\s*end\s*\}\}",
    re.DOTALL,
)

# Matches {{ re_replace .Variable "pattern" "replacement" }}
_RE_REPLACE_RE = re.compile(
    r"\{\{\s*re_replace\s+(\.\w+(?:\.\w+)*)\s+"
    r'"(.*?)"\s+"(.*?)"\s*\}\}'
)

# Matches {{ join .Variable "separator" }}
_JOIN_RE = re.compile(
    r'\{\{\s*join\s+(\.\w+(?:\.\w+)*)\s+"(.*?)"\s*\}\}'
)

# Logic functions: and, or with 2+ variable/literal args
_LOGIC_FUNCS = {"and", "or", "eq", "ne"}
_LOGIC_RE = re.compile(
    r'\b(and|or|eq|ne)((?:\s+(?:\(?\.[^\)\s]+\)?|"[^"]+"))+)'
)

# if ... else ... end (non-greedy, innermost first)
_IF_ELSE_RE = re.compile(
    r"\{\{\s*if\s+(.+?)\s*\}\}(.*?)\{\{\s*else\s*\}\}(.*?)\{\{\s*end\s*\}\}",
    re.DOTALL,
)

# if ... end (no else)
_IF_RE = re.compile(
    r"\{\{\s*if\s+(.+?)\s*\}\}(.*?)\{\{\s*end\s*\}\}",
    re.DOTALL,
)

# Simple variable: {{ .Foo.Bar }}
_VAR_RE = re.compile(r"\{\{\s*(\.\w+(?:\.\w+)*)\s*\}\}")


def _resolve_var(name: str, variables: dict[str, Any]) -> Any:
    """Resolve a dotted variable name against the variables dict.

    Variables are stored flat with dotted keys like ".Config.username".
    We try the full key first, then fall back to nested lookup.
    """
    # Direct lookup (most common case)
    if name in variables:
        return variables[name]

    # Try without leading dot
    no_dot = name.lstrip(".")
    prefixed = "." + no_dot
    if prefixed in variables:
        return variables[prefixed]

    # Nested lookup: .Config.username -> look for "Config" dict, then "username"
    parts = no_dot.split(".")
    current: Any = variables
    for part in parts:
        if isinstance(current, dict):
            # Try with and without dot prefix
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
    """Evaluate truthiness the Go template way."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict)):
        return len(value) > 0
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return bool(value)


def _extract_logic_args(args_str: str) -> list[str]:
    """Extract arguments from a logic function match."""
    args = []
    # Match either "string literal" or (.varname) or .varname
    for m in re.finditer(r'"([^"]+)"|(\(?\.[^\)\s]+\)?)', args_str):
        if m.group(1) is not None:
            args.append('"' + m.group(1) + '"')
        else:
            args.append(m.group(2).strip("()"))
    return args


def _eval_logic(func_name: str, args: list[str], variables: dict[str, Any]) -> str:
    """Evaluate a logic function."""
    if func_name == "and":
        result = args[-1] if args else ""
        for arg in args:
            if arg.startswith('"'):
                continue
            val = _resolve_var(arg, variables)
            if not _is_truthy(val):
                result = arg
                break
        return result

    if func_name == "or":
        result = args[-1] if args else ""
        for arg in args:
            if arg.startswith('"'):
                continue
            val = _resolve_var(arg, variables)
            if _is_truthy(val):
                result = arg
                break
        return result

    if func_name in ("eq", "ne"):
        if len(args) < 2:
            return ".False"
        vals = []
        for arg in args[:2]:
            if arg.startswith('"'):
                vals.append(arg.strip('"'))
            else:
                v = _resolve_var(arg, variables)
                vals.append(str(v) if v is not None else "")
        is_equal = vals[0] == vals[1]
        if func_name == "eq":
            return ".True" if is_equal else ".False"
        else:
            return ".True" if not is_equal else ".False"

    return ""


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

    result = template

    # 1. Range expressions: {{ range .Var }}...{{ . }}...{{ end }}
    def _expand_range(m: re.Match) -> str:
        var_name = m.group(1)
        body = m.group(2)
        items = _resolve_var(var_name, variables)
        if not items or not isinstance(items, (list, tuple)):
            return ""
        parts = []
        for item in items:
            expanded = body.replace("{{ . }}", str(item)).replace("{{.}}", str(item))
            parts.append(expanded)
        return "".join(parts)

    result = _RANGE_RE.sub(_expand_range, result)

    # 2. re_replace: {{ re_replace .Var "pattern" "replacement" }}
    def _expand_re_replace(m: re.Match) -> str:
        var_name = m.group(1)
        pattern = m.group(2)
        replacement = m.group(3)
        value = _resolve_var(var_name, variables)
        value = str(value) if value is not None else ""
        expanded = re.sub(pattern, replacement, value)
        if modifier:
            expanded = modifier(expanded)
        return expanded

    result = _RE_REPLACE_RE.sub(_expand_re_replace, result)

    # 3. join: {{ join .Var "," }}
    def _expand_join(m: re.Match) -> str:
        var_name = m.group(1)
        delimiter = m.group(2)
        items = _resolve_var(var_name, variables)
        if not items or not isinstance(items, (list, tuple)):
            return ""
        expanded = delimiter.join(str(i) for i in items)
        if modifier:
            expanded = modifier(expanded)
        return expanded

    result = _JOIN_RE.sub(_expand_join, result)

    # 4. Logic functions (and, or, eq, ne) - process iteratively for nesting
    max_iterations = 20
    for _ in range(max_iterations):
        m = _LOGIC_RE.search(result)
        if not m:
            break
        func_name = m.group(1)
        args_str = m.group(2)
        args = _extract_logic_args(args_str)
        func_result = _eval_logic(func_name, args, variables)
        # For eq/ne with >2 args, only consume the first 2
        if func_name in ("eq", "ne") and len(args) > 2:
            # Recalculate the match end to only consume 2 args
            consumed = result[m.start():m.end()]
            # Find position after 2nd arg
            arg_matches = list(re.finditer(r'"[^"]+"|(\(?\.[^\)\s]+\)?)', consumed))
            if len(arg_matches) >= 3:  # func name is not in these matches
                end_pos = m.start() + arg_matches[2].start()
                result = result[:m.start()] + func_result + result[end_pos:]
                continue
        result = result[:m.start()] + func_result + result[m.end():]

    # 5. if/else/end - process iteratively for nesting (innermost first)
    for _ in range(max_iterations):
        m = _IF_ELSE_RE.search(result)
        if not m:
            break
        condition = m.group(1).strip()
        on_true = m.group(2)
        on_false = m.group(3)

        if condition in (".True", "true", "True"):
            chosen = on_true
        elif condition in (".False", "false", "False", ""):
            chosen = on_false
        elif condition.startswith("."):
            val = _resolve_var(condition, variables)
            chosen = on_true if _is_truthy(val) else on_false
        else:
            # Could be a logic function result left as a variable ref
            val = _resolve_var(condition, variables)
            chosen = on_true if _is_truthy(val) else on_false

        result = result[:m.start()] + chosen + result[m.end():]

    # 5b. if/end (no else)
    for _ in range(max_iterations):
        m = _IF_RE.search(result)
        if not m:
            break
        condition = m.group(1).strip()
        body = m.group(2)

        if condition in (".True", "true", "True"):
            chosen = body
        elif condition in (".False", "false", "False", ""):
            chosen = ""
        elif condition.startswith("."):
            val = _resolve_var(condition, variables)
            chosen = body if _is_truthy(val) else ""
        else:
            val = _resolve_var(condition, variables)
            chosen = body if _is_truthy(val) else ""

        result = result[:m.start()] + chosen + result[m.end():]

    # 6. Simple variable substitution: {{ .Var }}
    def _expand_var(m: re.Match) -> str:
        var_name = m.group(1)
        val = _resolve_var(var_name, variables)
        if val is None:
            return ""
        expanded = str(val)
        if modifier:
            expanded = modifier(expanded)
        return expanded

    result = _VAR_RE.sub(_expand_var, result)

    return result
