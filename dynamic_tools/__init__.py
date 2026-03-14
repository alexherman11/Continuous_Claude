"""
Dynamic Tool Registry — Hot-loadable tool system for the Consciousness Engine.

Each .py file in dynamic_tools/enabled/ defines a tool with:
  SCHEMA = { "name": "...", "description": "...", "input_schema": {...} }
  def execute(tool_input, file_mgr, state): -> dict

Tools are reloaded every cycle via load_dynamic_tools(), so dropping a new
file into enabled/ makes it available on the next thought cycle — no restart needed.
"""

import importlib.util
import logging
import os
import py_compile
import sys

# Registry: { "tool_name": { "schema": {...}, "execute": callable } }
DYNAMIC_TOOL_REGISTRY = {}

_this_dir = os.path.dirname(os.path.abspath(__file__))
ENABLED_DIR = os.path.join(_this_dir, "enabled")
DISABLED_DIR = os.path.join(_this_dir, "disabled")


def load_dynamic_tools():
    """
    Scan dynamic_tools/enabled/ for .py files, import each, and register
    their SCHEMA + execute function. Called every cycle for hot-reload.

    Returns the updated DYNAMIC_TOOL_REGISTRY dict.
    """
    global DYNAMIC_TOOL_REGISTRY
    new_registry = {}

    if not os.path.isdir(ENABLED_DIR):
        DYNAMIC_TOOL_REGISTRY = new_registry
        return new_registry

    for filename in sorted(os.listdir(ENABLED_DIR)):
        if not filename.endswith(".py") or filename.startswith("_"):
            continue

        filepath = os.path.join(ENABLED_DIR, filename)
        tool_name = filename[:-3]  # strip .py

        try:
            # Syntax check first
            py_compile.compile(filepath, doraise=True)

            # Dynamic import (fresh each time — no caching)
            module_name = f"dynamic_tools.enabled.{tool_name}"
            spec = importlib.util.spec_from_file_location(module_name, filepath)
            module = importlib.util.module_from_spec(spec)

            # Remove from sys.modules to force fresh import
            if module_name in sys.modules:
                del sys.modules[module_name]

            spec.loader.exec_module(module)

            # Validate contract
            if not hasattr(module, "SCHEMA"):
                logging.warning(f"Dynamic tool {filename}: missing SCHEMA, skipping")
                continue
            if not hasattr(module, "execute"):
                logging.warning(f"Dynamic tool {filename}: missing execute(), skipping")
                continue
            if not callable(module.execute):
                logging.warning(f"Dynamic tool {filename}: execute is not callable, skipping")
                continue

            schema = module.SCHEMA
            required_keys = {"name", "description", "input_schema"}
            missing = required_keys - set(schema.keys())
            if missing:
                logging.warning(f"Dynamic tool {filename}: SCHEMA missing keys {missing}, skipping")
                continue

            # Use the name from the schema, not the filename
            registered_name = schema["name"]
            new_registry[registered_name] = {
                "schema": schema,
                "execute": module.execute,
                "file": filename,
                "path": filepath,
            }
            logging.debug(f"Dynamic tool loaded: {registered_name} from {filename}")

        except py_compile.PyCompileError as e:
            logging.error(f"Dynamic tool {filename}: syntax error: {e}")
        except Exception as e:
            logging.error(f"Dynamic tool {filename}: failed to load: {type(e).__name__}: {e}")

    DYNAMIC_TOOL_REGISTRY = new_registry
    return new_registry


def get_dynamic_schemas():
    """Return list of SCHEMA dicts for all loaded dynamic tools."""
    return [entry["schema"] for entry in DYNAMIC_TOOL_REGISTRY.values()]


def execute_dynamic_tool(name, tool_input, file_mgr, state):
    """
    Execute a dynamic tool by name. Returns None if tool not found.
    Returns the result dict if found and executed.
    """
    entry = DYNAMIC_TOOL_REGISTRY.get(name)
    if entry is None:
        return None
    return entry["execute"](tool_input, file_mgr, state)


def list_dynamic_tools():
    """Return info about all loaded dynamic tools."""
    return {
        name: {
            "file": entry["file"],
            "description": entry["schema"].get("description", ""),
        }
        for name, entry in DYNAMIC_TOOL_REGISTRY.items()
    }


def disable_tool(tool_name):
    """
    Move a dynamic tool from enabled/ to disabled/.
    Returns status dict.
    """
    entry = DYNAMIC_TOOL_REGISTRY.get(tool_name)
    if entry is None:
        return {"error": f"Dynamic tool '{tool_name}' not found in registry"}

    src = entry["path"]
    dst = os.path.join(DISABLED_DIR, entry["file"])
    os.makedirs(DISABLED_DIR, exist_ok=True)

    try:
        os.rename(src, dst)
    except OSError as e:
        return {"error": f"Failed to move tool file: {e}"}

    # Remove from registry
    del DYNAMIC_TOOL_REGISTRY[tool_name]
    return {
        "status": "ok",
        "tool": tool_name,
        "moved_to": f"dynamic_tools/disabled/{entry['file']}",
    }
