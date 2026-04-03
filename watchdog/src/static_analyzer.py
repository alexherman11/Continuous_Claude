"""
AST-based static analysis engine for the watchdog.

Walks Python source files, builds an AST, and checks for dangerous patterns
including eval/exec, subprocess, ctypes, raw sockets, and more.

Supports aliased imports (e.g. `import subprocess as sp; sp.run(...)`)
and attribute chain resolution.
"""

import ast
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

EXCLUDE_DIRS = {"__pycache__", ".git", "node_modules", ".venv", "venv", "sandbox"}


class DangerousPatternVisitor(ast.NodeVisitor):
    """AST visitor that detects dangerous function calls and imports."""

    def __init__(self, source_lines, filename):
        self.source_lines = source_lines
        self.filename = filename
        self.findings = []
        self.imports = {}  # alias -> fully qualified module name

    def _add_finding(self, severity, pattern_id, description, node, context=None):
        line_content = ""
        if 1 <= node.lineno <= len(self.source_lines):
            line_content = self.source_lines[node.lineno - 1].strip()

        self.findings.append({
            "severity": severity,
            "pattern_id": pattern_id,
            "description": description,
            "file": self.filename,
            "line": node.lineno,
            "col": node.col_offset,
            "source_line": line_content,
            "context": context,
        })

    def visit_Import(self, node):
        for alias in node.names:
            local_name = alias.asname if alias.asname else alias.name
            self.imports[local_name] = alias.name

            root_module = alias.name.split(".")[0]
            if root_module == "ctypes":
                self._add_finding("critical", "ctypes_import",
                                  f"Import of ctypes module", node)
            elif root_module == "importlib":
                self._add_finding("high", "importlib_import",
                                  f"Import of importlib module", node)
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        if node.module:
            for alias in node.names:
                local_name = alias.asname if alias.asname else alias.name
                self.imports[local_name] = f"{node.module}.{alias.name}"

            root_module = node.module.split(".")[0]
            if root_module == "ctypes":
                self._add_finding("critical", "ctypes_import",
                                  f"Import from {node.module}", node)
            elif root_module == "importlib":
                self._add_finding("high", "importlib_import",
                                  f"Import from {node.module}", node)
        self.generic_visit(node)

    def visit_Call(self, node):
        func_name = self._resolve_call_name(node)
        if func_name:
            self._check_function_call(func_name, node)
        self.generic_visit(node)

    def _resolve_call_name(self, node):
        """Resolve a Call node to a dotted function name, resolving aliases."""
        if isinstance(node.func, ast.Name):
            name = node.func.id
            # Resolve through import aliases for bare names
            if name in self.imports:
                return self.imports[name]
            return name
        elif isinstance(node.func, ast.Attribute):
            parts = []
            current = node.func
            while isinstance(current, ast.Attribute):
                parts.append(current.attr)
                current = current.value
            if isinstance(current, ast.Name):
                parts.append(current.id)
                parts.reverse()
                # Resolve the root through import aliases
                if parts[0] in self.imports:
                    resolved = self.imports[parts[0]]
                    return resolved + "." + ".".join(parts[1:])
                return ".".join(parts)
        return None

    def _check_function_call(self, func_name, node):
        """Check a resolved function name against dangerous patterns."""
        # Normalize: get the leaf function name and full path
        parts = func_name.split(".")
        leaf = parts[-1] if parts else func_name

        # --- CRITICAL ---

        # eval, exec, compile as bare calls
        if func_name in ("eval", "exec", "compile"):
            self._add_finding("critical", "eval_exec",
                              f"Call to {func_name}()", node)
            return

        # builtins.eval, builtins.exec, builtins.compile
        if leaf in ("eval", "exec", "compile") and len(parts) == 2 and parts[0] == "builtins":
            self._add_finding("critical", "eval_exec",
                              f"Call to {func_name}()", node)
            return

        # __import__
        if func_name == "__import__":
            self._add_finding("critical", "dunder_import",
                              "Dynamic import via __import__()", node)
            return

        # os.system and family
        OS_EXEC_FUNCS = {
            "system", "popen", "execl", "execle", "execlp", "execlpe",
            "execv", "execve", "execvp", "execvpe", "spawnl", "spawnle",
        }
        if self._matches_module(func_name, "os", OS_EXEC_FUNCS):
            self._add_finding("critical", "os_system",
                              f"Shell execution via {func_name}()", node)
            return

        # subprocess.*
        SUBPROCESS_FUNCS = {
            "run", "call", "check_call", "check_output", "Popen",
            "getoutput", "getstatusoutput",
        }
        if self._matches_module(func_name, "subprocess", SUBPROCESS_FUNCS):
            self._add_finding("critical", "subprocess",
                              f"Subprocess call: {func_name}()", node)
            return

        # ctypes.*
        if self._root_module(func_name) == "ctypes":
            self._add_finding("critical", "ctypes",
                              f"C FFI call: {func_name}()", node)
            return

        # raw sockets
        SOCKET_FUNCS = {"socket", "create_connection", "create_server"}
        if self._matches_module(func_name, "socket", SOCKET_FUNCS):
            self._add_finding("critical", "raw_socket",
                              f"Raw socket: {func_name}()", node)
            return

        # --- HIGH ---

        # base64 decode
        if func_name in ("base64.b64decode", "base64.decodebytes"):
            self._add_finding("high", "base64_decode",
                              f"Base64 decoding: {func_name}()", node)
            return

        # pickle deserialization
        if func_name in ("pickle.loads", "pickle.load"):
            self._add_finding("high", "pickle_load",
                              f"Pickle deserialization: {func_name}()", node)
            return

        # getattr/setattr with computed (non-literal) attribute name
        if func_name in ("getattr", "setattr", "delattr"):
            if len(node.args) >= 2 and not isinstance(node.args[1], ast.Constant):
                self._add_finding("high", "getattr_setattr",
                                  f"Dynamic attribute access: {func_name}() "
                                  f"with computed name", node)
            return

        # os.environ access / os.putenv / os.unsetenv
        if func_name in ("os.putenv", "os.unsetenv"):
            self._add_finding("high", "env_modification",
                              f"Environment modification: {func_name}()", node)
            return

    def visit_Attribute(self, node):
        """Catch os.environ access (attribute, not call)."""
        if isinstance(node.value, ast.Name):
            full_name = f"{node.value.id}.{node.attr}"
            # Resolve alias
            if node.value.id in self.imports:
                full_name = f"{self.imports[node.value.id]}.{node.attr}"

            if full_name == "os.environ":
                self._add_finding("high", "env_modification",
                                  f"Environment access: os.environ", node)
        self.generic_visit(node)

    def visit_Constant(self, node):
        """Check string constants for suspicious system paths."""
        if isinstance(node.value, str):
            for prefix in ("/etc/", "/proc/", "/sys/", "/dev/"):
                if prefix in node.value:
                    self._add_finding("high", "system_path_access",
                                      f"Reference to system path: '{node.value}'",
                                      node)
                    break
        self.generic_visit(node)

    def _matches_module(self, func_name, module, func_set):
        """Check if func_name is module.func where func is in func_set."""
        parts = func_name.split(".")
        if len(parts) >= 2:
            root = self._root_module(func_name)
            leaf = parts[-1]
            return root == module and leaf in func_set
        return False

    def _root_module(self, func_name):
        """Get the root module of a dotted name."""
        return func_name.split(".")[0]


def analyze_file(filepath):
    """Analyze a single Python file. Returns list of findings."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            source = f.read()
            source_lines = source.split("\n")
    except (UnicodeDecodeError, OSError) as e:
        return [{"severity": "error", "pattern_id": "read_error",
                 "description": f"Could not read file: {e}",
                 "file": str(filepath), "line": 0}]

    try:
        tree = ast.parse(source, filename=str(filepath))
    except SyntaxError as e:
        return [{"severity": "error", "pattern_id": "syntax_error",
                 "description": f"Syntax error: {e}",
                 "file": str(filepath), "line": e.lineno or 0}]

    visitor = DangerousPatternVisitor(source_lines, str(filepath))
    visitor.visit(tree)
    return visitor.findings


def analyze_directory(dirpath, exclude_dirs=None):
    """Analyze all Python files in a directory tree."""
    if exclude_dirs is None:
        exclude_dirs = EXCLUDE_DIRS

    all_findings = []
    files_analyzed = 0

    for pyfile in Path(dirpath).rglob("*.py"):
        if any(excluded in pyfile.parts for excluded in exclude_dirs):
            continue
        findings = analyze_file(str(pyfile))
        all_findings.extend(findings)
        files_analyzed += 1

    return _summarize(all_findings, files_analyzed)


def analyze_diff_only(dirpath, changed_files):
    """Analyze only the files that changed (faster for incremental checks)."""
    all_findings = []
    files_analyzed = 0

    for filepath in changed_files:
        full_path = Path(dirpath) / filepath
        if full_path.suffix == ".py" and full_path.exists():
            findings = analyze_file(str(full_path))
            all_findings.extend(findings)
            files_analyzed += 1

    return _summarize(all_findings, files_analyzed)


def _summarize(findings, files_analyzed):
    """Build a summary dict from a list of findings."""
    return {
        "files_analyzed": files_analyzed,
        "total_findings": len(findings),
        "by_severity": {
            "critical": len([f for f in findings if f["severity"] == "critical"]),
            "high": len([f for f in findings if f["severity"] == "high"]),
            "moderate": len([f for f in findings if f["severity"] == "moderate"]),
            "error": len([f for f in findings if f["severity"] == "error"]),
        },
        "findings": findings,
    }
