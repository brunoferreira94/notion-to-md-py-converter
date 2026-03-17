import ast
import json
import os
import re
from pathlib import Path

repo_root = Path(__file__).parent
files = [
    repo_root / 'notion_converter.py',
    repo_root / 'notion_utils.py',
    repo_root / 'convert_from_public.py',
    repo_root / 'renderers.py',
]

diagnostics = []
files_checked = []

# helper
def ctx_lines(lines, idx, before=2, after=2):
    start = max(0, idx-before)
    end = min(len(lines), idx+after+1)
    return ''.join(f"{i+1}. {lines[i]}" for i in range(start, end))

for fpath in files:
    if not fpath.exists():
        continue
    files_checked.append(str(fpath))
    text = fpath.read_text(encoding='utf-8')
    lines = text.splitlines()

    # Syntax check
    try:
        tree = ast.parse(text, filename=str(fpath))
    except SyntaxError as e:
        diagnostics.append({
            'id': 'SYNTAX_ERROR',
            'file': str(fpath),
            'start_line': e.lineno or 1,
            'end_line': e.lineno or 1,
            'message': f'SyntaxError: {e.msg}',
            'severity': 'error',
            'recommended_change': 'Fix syntax error'
        })
        continue

    # Search for html.parser literal occurrences
    for i, line in enumerate(lines):
        if 'html.parser' in line:
            diagnostics.append({
                'id': 'GREP_HTML_PARSER',
                'file': str(fpath),
                'start_line': i+1,
                'end_line': i+1,
                'message': "Found literal 'html.parser'",
                'severity': 'info',
                'recommended_change': 'Ensure BeautifulSoup is available before calling or guard calls.'
            })

    # Find regex patterns used in re.compile / re.search / re.findall
    for match in re.finditer(r"re\.(compile|search|findall|match)\s*\(\s*(r?['\"])(.*?)\2", text, flags=re.S):
        full = match.group(0)
        pattern = match.group(3)
        # find line number
        lineno = text[:match.start()].count('\n') + 1
        diagnostics.append({
            'id': 'REGEX_USE',
            'file': str(fpath),
            'start_line': lineno,
            'end_line': lineno,
            'message': f'Regex usage: {pattern}',
            'severity': 'info',
            'recommended_change': 'Review regex for correctness and potential catastrophic backtracking.'
        })

    # String literal duplication (simple heuristic) - S1192
    str_counts = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            s = node.value.strip()
            if len(s) >= 3:
                str_counts.setdefault(s, []).append((node.lineno, node.col_offset))
    for s, occ in str_counts.items():
        if len(occ) > 1:
            first = occ[0][0]
            diagnostics.append({
                'id': 'S1192',
                'file': str(fpath),
                'start_line': occ[0][0],
                'end_line': occ[-1][0],
                'message': f"String literal duplicated {len(occ)} times: {s[:60]!r}",
                'severity': 'minor',
                'recommended_change': 'Extract duplicated string into a named constant.'
            })

    # Cognitive complexity-ish metric S3776 - heuristic: count branching nodes per function
    class CCVisitor(ast.NodeVisitor):
        def __init__(self):
            self.funcs = {}
            self._in_function = False
            self.count = 0
        def visit_FunctionDef(self, node):
            prev_in = self._in_function
            prev_count = self.count
            self._in_function = True
            self.count = 1
            self.generic_visit(node)
            self.funcs[node.name] = (self.count, node.lineno)
            self._in_function = prev_in
            self.count = prev_count
        def _inc(self, n=1):
            if self._in_function:
                self.count += n
        def visit_If(self, node):
            self._inc(1)
            self.generic_visit(node)
        def visit_For(self, node):
            self._inc(1)
            self.generic_visit(node)
        def visit_While(self, node):
            self._inc(1)
            self.generic_visit(node)
        def visit_Try(self, node):
            # try/except increases complexity
            self._inc(1)
            self.generic_visit(node)
        def visit_BoolOp(self, node):
            # and/or
            self._inc(len(node.values) - 1)
            self.generic_visit(node)
        def visit_With(self, node):
            self._inc(1)
            self.generic_visit(node)
        def visit_IfExp(self, node):
            self._inc(1)
            self.generic_visit(node)

    visitor = CCVisitor()
    visitor.visit(tree)
    for fname, (cc, lineno) in visitor.funcs.items():
        if cc > 15:
            diagnostics.append({
                'id': 'S3776',
                'file': str(fpath),
                'start_line': lineno,
                'end_line': lineno,
                'message': f'Function {fname!r} has heuristic cognitive complexity {cc}',
                'severity': 'major',
                'recommended_change': 'Refactor function into smaller units to reduce complexity.'
            })

    # Heuristic optional-call detection: find assignments with 'or None' or explicit None defaults
    optional_vars = set()
    for i, line in enumerate(lines):
        if re.search(r"=\s*.*\sor\sNone", line):
            m = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
            if m:
                optional_vars.add(m.group(1))
        # assignments like "X = None" from imports
        m2 = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*None\b", line)
        if m2:
            optional_vars.add(m2.group(1))

    # Find calls where func is one of optional_vars
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in optional_vars:
                diagnostics.append({
                    'id': 'OPTIONAL_CALL',
                    'file': str(fpath),
                    'start_line': node.lineno,
                    'end_line': node.lineno,
                    'message': f"Call of possibly-None object '{func.id}'",
                    'severity': 'warning',
                    'recommended_change': f"Guard the call: if {func.id} is not None: {func.id}(...)",
                })

# Specific verification for notion_converter.py lines ~110,186,192,232
nc = repo_root / 'notion_converter.py'
if nc.exists():
    txt = nc.read_text(encoding='utf-8')
    nlines = txt.splitlines()
    targets = [110, 186, 192, 232]
    for t in targets:
        if 1 <= t <= len(nlines):
            snippet = ctx_lines(nlines, t-1, before=2, after=2)
            line = nlines[t-1].strip()
            # determine if BeautifulSoup might be None by checking assignment at top
            bs_none = any(re.match(r"\s*BeautifulSoup\s*=\s*None\b", l) for l in nlines[:40])
            msg = f"Call to BeautifulSoup on line {t}: '{line}'"
            severity = 'warning' if bs_none else 'info'
            rec = "Ensure BeautifulSoup is imported; guard call with 'if BeautifulSoup is not None:' or check BS4_AVAILABLE. Example: if BeautifulSoup is not None: soup = BeautifulSoup(html, 'html.parser')"
            diagnostics.append({
                'id': 'PYLANCE_OPTIONAL_CALL',
                'file': str(nc),
                'start_line': t,
                'end_line': t,
                'message': msg,
                'severity': severity,
                'recommended_change': rec,
                'code_snippet': snippet
            })

# dedupe diagnostics by key
seen = set()
unique = []
for d in diagnostics:
    key = (d.get('id'), d.get('file'), d.get('start_line'), d.get('end_line'), d.get('message'))
    if key in seen:
        continue
    seen.add(key)
    unique.append(d)

output = {
    'diagnostics': unique,
    'files_checked': files_checked,
}
print(json.dumps(output, ensure_ascii=False, indent=2))
