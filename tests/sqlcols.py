"""Read the output column names of a report query straight from its SQL text (no database needed).

Used by the tests to prove that sql/kpi_dashboard.sql and sql/supply_demand.sql return the columns the dashboard's
contract lists. It understands what these files use: comments, string literals, quoted identifiers, parentheses,
CASE ... END, WITH ... AS ( ... ), and UNION ALL branches.
"""
import re

_TOKEN = re.compile(
    r"""(?P<comment>--[^\n]*|/\*.*?\*/)
      | (?P<string>'(?:[^']|'')*')
      | (?P<quoted>"(?:[^"]|"")*")
      | (?P<word>[A-Za-z_][A-Za-z0-9_$#]*)
      | (?P<other>\S)""",
    re.S | re.X,
)


def _tokens(sql):
    for m in _TOKEN.finditer(sql):
        kind = m.lastgroup
        if kind == "comment":
            continue
        yield kind, m.group(kind)


def _unquote(tok_kind, tok):
    return tok[1:-1].replace('""', '"') if tok_kind == "quoted" else tok


def select_lists(sql):
    """The select lists of every top-level SELECT of the final statement (each UNION branch), in order.

    Returns a list of lists of output column names. WITH clauses are skipped: only the main query counts.
    """
    toks = list(_tokens(sql))
    depth = 0
    i = 0
    # Skip a leading WITH name AS ( ... ), name AS ( ... ) block.
    if toks and toks[0][1].upper() == "WITH":
        i = 1
        while i < len(toks):
            if toks[i][1] == "(":
                d = 0
                while i < len(toks):
                    if toks[i][1] == "(":
                        d += 1
                    elif toks[i][1] == ")":
                        d -= 1
                        if d == 0:
                            break
                    i += 1
                i += 1
                if i < len(toks) and toks[i][1] == ",":
                    i += 1
                    continue
                break
            i += 1
    branches = []
    while i < len(toks):
        kind, tok = toks[i]
        if tok == "(":
            depth += 1
        elif tok == ")":
            depth -= 1
        elif depth == 0 and kind == "word" and tok.upper() == "SELECT":
            items, i = _read_select_list(toks, i + 1)
            branches.append(items)
            continue
        i += 1
    return branches


def _read_select_list(toks, i):
    """From just after SELECT to the top-level FROM: split on top-level commas, take each item's output name."""
    items, current, depth, case_depth = [], [], 0, 0
    while i < len(toks):
        kind, tok = toks[i]
        up = tok.upper() if kind == "word" else tok
        if tok == "(":
            depth += 1
        elif tok == ")":
            depth -= 1
        elif kind == "word" and up == "CASE":
            case_depth += 1
        elif kind == "word" and up == "END" and case_depth:
            case_depth -= 1
        if depth == 0 and case_depth == 0 and kind == "word" and up == "FROM":
            break
        if depth == 0 and case_depth == 0 and tok == ",":
            items.append(_output_name(current))
            current = []
        else:
            current.append((kind, tok))
        i += 1
    if current:
        items.append(_output_name(current))
    return items, i


def _output_name(item):
    """`expr AS "Alias"` -> Alias; `T0."Col"` -> Col; `*` stays `*`."""
    if not item:
        return ""
    words = [t for t in item]
    for j in range(len(words) - 2, -1, -1):
        if words[j][0] == "word" and words[j][1].upper() == "AS":
            return _unquote(*words[j + 1])
    kind, tok = words[-1]
    return _unquote(kind, tok)


def columns(sql):
    """Output columns of the query: the first branch's names (UNION branches must all have the same count)."""
    branches = select_lists(sql)
    if not branches:
        raise ValueError("No SELECT found.")
    counts = {len(b) for b in branches}
    if len(counts) != 1:
        raise ValueError(f"UNION branches return different column counts: {[len(b) for b in branches]}")
    return branches[0]
