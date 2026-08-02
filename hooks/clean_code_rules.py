"""Readability rules applied to a single source file.

Pure analysis: no I/O, no process control. The entry point is `analyse`.
The brace-language checks are a line scanner, not a parser, so they are
approximate by design and never raise on malformed input.
"""

from __future__ import annotations

import ast
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

PYTHON_SUFFIXES = frozenset({".py", ".pyi"})
BRACE_SUFFIXES = frozenset(
    {
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".java",
        ".kt",
        ".cs",
        ".go",
        ".c",
        ".cc",
        ".cpp",
        ".h",
        ".hpp",
        ".rs",
        ".swift",
    }
)

DEFAULT_LIMITS = {
    "MAX_FUNCTION_LINES": 20,
    "MAX_PARAMETERS": 3,
    "MAX_NESTING_DEPTH": 3,
    "MAX_BRANCHES": 10,
    "MAX_FILE_LINES": 400,
    "MAX_LINE_LENGTH": 120,
}

DEFAULT_IGNORE = (
    "*/node_modules/*",
    "*/.venv/*",
    "*/venv/*",
    "*/dist/*",
    "*/build/*",
    "*/migrations/*",
    "*_pb2.py",
    "*.min.js",
    "*/vendor/*",
)

MARKER_WORDS = ("TODO", "FIXME", "HACK", "XXX")
MARKER_PATTERN = re.compile(r"\b(" + "|".join(MARKER_WORDS) + r")\b")
TICKET_PATTERN = re.compile(r"[A-Z]{2,}-\d+|#\d+|https?://")
COMMENT_START_PATTERN = re.compile(r"(^\s*(#|//|/\*|\*))|(\s(#|//)\s)")
COMMENTED_CODE_PATTERN = re.compile(
    r"^\s*(#|//)\s*"
    r"(if |for |while |return\b|def |class |function |var |let |const |"
    r"import |from |public |private |[\w.]+\s*=\s*[^=]|[\w.]+\([^)]*\)\s*;?)"
)
SIGNATURE_PATTERN = re.compile(
    r"^[^/*#]*\b(function\s+\w+|\w+\s*\([^;]*\)\s*(:\s*[\w<>\[\], ]+)?)\s*\{\s*$"
)

PYTHON_BLOCKS = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.With,
    ast.AsyncWith,
    ast.Try,
    ast.Match,
)
PYTHON_DECISIONS = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.ExceptHandler,
    ast.BoolOp,
    ast.IfExp,
    ast.match_case,
)


@dataclass(frozen=True)
class Finding:
    """A single violation, anchored to a line."""

    line: int
    rule: str
    message: str

    def render(self) -> str:
        return f"  L{self.line}: [{self.rule}] {self.message}"


def load_limits() -> dict[str, int]:
    """Read limits from the environment, falling back to the defaults."""
    limits = {}
    for name, fallback in DEFAULT_LIMITS.items():
        raw = os.environ.get(f"CLEAN_CODE_GUARD_{name}", "")
        limits[name] = int(raw) if raw.isdigit() else fallback
    return limits


def ignore_patterns() -> tuple[str, ...]:
    extra = os.environ.get("CLEAN_CODE_GUARD_IGNORE", "")
    custom = tuple(p.strip() for p in extra.split(os.pathsep) if p.strip())
    return DEFAULT_IGNORE + custom


def is_supported(path: Path) -> bool:
    return path.suffix in PYTHON_SUFFIXES or path.suffix in BRACE_SUFFIXES


def analyse(path: Path, source: str, limits: dict[str, int]) -> list[Finding]:
    """Return every finding for one file, ordered by line then rule."""
    findings = text_findings(source, limits)
    if path.suffix in PYTHON_SUFFIXES:
        findings += python_findings(source, limits)
    else:
        findings += brace_findings(source, limits)
    return sorted(findings, key=lambda finding: (finding.line, finding.rule))


# --------------------------------------------------------------------------- #
# Language independent
# --------------------------------------------------------------------------- #


def text_findings(source: str, limits: dict[str, int]) -> list[Finding]:
    lines = source.splitlines()
    findings = file_length_findings(len(lines), limits)
    for number, text in enumerate(lines, start=1):
        findings += line_findings(number, text, limits)
    return findings


def file_length_findings(count: int, limits: dict[str, int]) -> list[Finding]:
    if count <= limits["MAX_FILE_LINES"]:
        return []
    return [
        Finding(
            line=1,
            rule="file-length",
            message=f"{count} lines, limit {limits['MAX_FILE_LINES']}. Split by responsibility.",
        )
    ]


def line_findings(number: int, text: str, limits: dict[str, int]) -> list[Finding]:
    checks = (long_line_finding, commented_code_finding, orphan_marker_finding)
    found = [check(number, text, limits) for check in checks]
    return [finding for finding in found if finding is not None]


def long_line_finding(number: int, text: str, limits: dict[str, int]) -> Finding | None:
    if len(text) <= limits["MAX_LINE_LENGTH"]:
        return None
    return Finding(
        line=number,
        rule="line-length",
        message=f"{len(text)} characters, limit {limits['MAX_LINE_LENGTH']}.",
    )


def commented_code_finding(
    number: int, text: str, _limits: dict[str, int]
) -> Finding | None:
    if not COMMENTED_CODE_PATTERN.match(text):
        return None
    return Finding(
        line=number,
        rule="commented-code",
        message="Commented-out code. Delete it, version control keeps history.",
    )


def orphan_marker_finding(
    number: int, text: str, _limits: dict[str, int]
) -> Finding | None:
    if not COMMENT_START_PATTERN.search(text) or not MARKER_PATTERN.search(text):
        return None
    if TICKET_PATTERN.search(text):
        return None
    return Finding(
        line=number,
        rule="orphan-marker",
        message="Marker without a ticket or link. Reference an issue or resolve it.",
    )


# --------------------------------------------------------------------------- #
# Python
# --------------------------------------------------------------------------- #


def python_findings(source: str, limits: dict[str, int]) -> list[Finding]:
    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        return [
            Finding(
                line=error.lineno or 1,
                rule="parse-error",
                message=f"File does not parse: {error.msg}.",
            )
        ]
    return python_node_findings(tree, limits)


def python_node_findings(tree: ast.AST, limits: dict[str, int]) -> list[Finding]:
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            findings += function_findings(node, limits)
        elif isinstance(node, ast.ExceptHandler) and is_silent_handler(node):
            findings.append(silent_handler_finding(node))
    return findings


def function_findings(node: ast.AST, limits: dict[str, int]) -> list[Finding]:
    checks = (
        length_finding,
        parameters_finding,
        depth_finding,
        branches_finding,
        flag_finding,
    )
    found = [check(node, limits) for check in checks]
    return [finding for finding in found if finding is not None]


def length_finding(node: ast.AST, limits: dict[str, int]) -> Finding | None:
    length = (node.end_lineno or node.lineno) - node.lineno + 1
    if length <= limits["MAX_FUNCTION_LINES"]:
        return None
    return Finding(
        line=node.lineno,
        rule="function-length",
        message=f"'{node.name}' is {length} lines, limit "
        f"{limits['MAX_FUNCTION_LINES']}. Extract the deepest block.",
    )


def parameters_finding(node: ast.AST, limits: dict[str, int]) -> Finding | None:
    count = parameter_count(node)
    if count <= limits["MAX_PARAMETERS"]:
        return None
    return Finding(
        line=node.lineno,
        rule="parameter-count",
        message=f"'{node.name}' takes {count} parameters, limit "
        f"{limits['MAX_PARAMETERS']}. Group them into an object.",
    )


def depth_finding(node: ast.AST, limits: dict[str, int]) -> Finding | None:
    depth = nesting_depth(node)
    if depth <= limits["MAX_NESTING_DEPTH"]:
        return None
    return Finding(
        line=node.lineno,
        rule="nesting-depth",
        message=f"'{node.name}' nests {depth} levels, limit "
        f"{limits['MAX_NESTING_DEPTH']}. Use guard clauses.",
    )


def branches_finding(node: ast.AST, limits: dict[str, int]) -> Finding | None:
    branches = branch_count(node)
    if branches <= limits["MAX_BRANCHES"]:
        return None
    return Finding(
        line=node.lineno,
        rule="branch-count",
        message=f"'{node.name}' has {branches} decision points, limit "
        f"{limits['MAX_BRANCHES']}. Split the responsibilities.",
    )


def flag_finding(node: ast.AST, _limits: dict[str, int]) -> Finding | None:
    if not has_boolean_flag(node):
        return None
    return Finding(
        line=node.lineno,
        rule="flag-parameter",
        message=f"'{node.name}' takes a boolean flag. Split it into two named functions.",
    )


def silent_handler_finding(node: ast.ExceptHandler) -> Finding:
    return Finding(
        line=node.lineno,
        rule="swallowed-error",
        message="Exception caught and ignored. Handle it or let it propagate.",
    )


def parameter_count(node: ast.AST) -> int:
    args = node.args
    positional = [a.arg for a in args.posonlyargs + args.args]
    visible = [name for name in positional if name not in {"self", "cls"}]
    return len(visible) + len(args.kwonlyargs)


def nesting_depth(node: ast.AST, depth: int = 0) -> int:
    deepest = depth
    for child in ast.iter_child_nodes(node):
        child_depth = depth + 1 if isinstance(child, PYTHON_BLOCKS) else depth
        deepest = max(deepest, nesting_depth(child, child_depth))
    return deepest


def branch_count(node: ast.AST) -> int:
    return sum(1 for child in ast.walk(node) if isinstance(child, PYTHON_DECISIONS))


def has_boolean_flag(node: ast.AST) -> bool:
    defaults = node.args.defaults + [d for d in node.args.kw_defaults if d]
    return any(
        isinstance(d, ast.Constant) and isinstance(d.value, bool) for d in defaults
    )


def is_silent_handler(node: ast.ExceptHandler) -> bool:
    return all(isinstance(statement, ast.Pass) for statement in node.body)


# --------------------------------------------------------------------------- #
# Brace languages
# --------------------------------------------------------------------------- #


@dataclass
class BraceScanner:
    """Tracks brace depth across lines to approximate function boundaries."""

    limits: dict[str, int]
    depth: int = 0
    open_functions: list[tuple[int, int]] = field(default_factory=list)

    def scan_line(self, number: int, text: str) -> list[Finding]:
        code = strip_comment(text)
        findings = self.open_function(number, code)
        self.depth = max(self.depth + code.count("{") - code.count("}"), 0)
        findings += deep_block_findings(number, self.depth, self.limits)
        return findings + self.close_functions(number)

    def open_function(self, number: int, code: str) -> list[Finding]:
        if not SIGNATURE_PATTERN.match(code):
            return []
        self.open_functions.append((number, self.depth))
        return signature_findings(number, code, self.limits)

    def close_functions(self, number: int) -> list[Finding]:
        findings: list[Finding] = []
        while self.open_functions and self.depth <= self.open_functions[-1][1]:
            start, _ = self.open_functions.pop()
            findings += function_length_findings(start, number - start + 1, self.limits)
        return findings


def brace_findings(source: str, limits: dict[str, int]) -> list[Finding]:
    scanner = BraceScanner(limits)
    findings: list[Finding] = []
    for number, text in enumerate(source.splitlines(), start=1):
        findings += scanner.scan_line(number, text)
    return findings


def deep_block_findings(
    number: int, depth: int, limits: dict[str, int]
) -> list[Finding]:
    allowed = limits["MAX_NESTING_DEPTH"] + 1
    if depth <= allowed:
        return []
    return [
        Finding(
            line=number,
            rule="nesting-depth",
            message=f"Block nests {depth} levels, limit {allowed}. Extract or return early.",
        )
    ]


def function_length_findings(
    start: int, length: int, limits: dict[str, int]
) -> list[Finding]:
    if length <= limits["MAX_FUNCTION_LINES"]:
        return []
    return [
        Finding(
            line=start,
            rule="function-length",
            message=f"Function is {length} lines, limit {limits['MAX_FUNCTION_LINES']}. "
            "Extract the deepest block.",
        )
    ]


def signature_findings(number: int, code: str, limits: dict[str, int]) -> list[Finding]:
    inside = code[code.find("(") + 1 : code.rfind(")")]
    parameters = [p for p in inside.split(",") if p.strip()]
    if len(parameters) <= limits["MAX_PARAMETERS"]:
        return []
    return [
        Finding(
            line=number,
            rule="parameter-count",
            message=f"Function takes {len(parameters)} parameters, limit "
            f"{limits['MAX_PARAMETERS']}. Group them into an object.",
        )
    ]


def strip_comment(text: str) -> str:
    for token in ("//", "#"):
        position = text.find(token)
        if position != -1:
            text = text[:position]
    return text
