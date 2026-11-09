"""change_manager: tier classification and static contract validation."""

import pytest

import change_manager as cm


@pytest.mark.parametrize("target,tier", [
    ("dynamic_tools/enabled/my_tool.py", 1),
    ("dynamic_tools\\enabled\\my_tool.py", 1),
    ("dynamic_tools/__init__.py", 3),            # the loader itself
    ("dynamic_tools/enabled/_private.py", 3),
    ("dynamic_tools/enabled/sub/x.py", 3),
    ("dynamic_tools/../some/other/file.py", 3),
    ("dynamic_tools/enabled/../../deploy/x.sh", 3),
    ("dynamic_tools/enabled/../../tool_definitions.py", 2),
    ("tool_definitions.py", 2),
    ("consciousness_server.py", 3),
    ("/etc/passwd", 3),
    ("../outside.py", 3),
    ("", 3),
])
def test_determine_tier(target, tier):
    assert cm._determine_tier(target) == tier


def test_normalize_rejects_escape():
    assert cm._normalize_target("../x.py")[0] is None
    assert cm._normalize_target("/abs/x.py")[0] is None
    assert cm._normalize_target("a/../../x.py")[0] is None
    assert cm._normalize_target("dynamic_tools/./enabled/x.py") == ("dynamic_tools/enabled/x.py", None)


GOOD_TOOL = '''
SCHEMA = {"name": "t", "description": "d", "input_schema": {"type": "object"}}

def execute(tool_input, file_mgr, state):
    return {"ok": True}
'''


def test_contract_validation_does_not_execute_code(tmp_path):
    marker = tmp_path / "executed"
    src = tmp_path / "tool.py"
    src.write_text(f"open({str(marker)!r}, 'w').write('x')\n" + GOOD_TOOL)
    assert cm._validate_dynamic_tool_contract(str(src)) == (True, None)
    assert not marker.exists()


@pytest.mark.parametrize("body,needle", [
    ("def execute(i, f, s): pass\n", "Missing module-level SCHEMA"),
    ("NAME = 't'\nSCHEMA = {'name': NAME, 'description': 'd', 'input_schema': {}}\n"
     "def execute(i, f, s): pass\n", "literal"),
    ("SCHEMA = {'name': 't'}\ndef execute(i, f, s): pass\n", "missing keys"),
    ("SCHEMA = {'name': 't', 'description': 'd', 'input_schema': {}}\n", "execute"),
    ("SCHEMA = [1]\ndef execute(i, f, s): pass\n", "must be a dict"),
    ("def broken(:\n", "Syntax error"),
])
def test_contract_validation_failures(tmp_path, body, needle):
    src = tmp_path / "tool.py"
    src.write_text(body)
    ok, err = cm._validate_dynamic_tool_contract(str(src))
    assert not ok and needle in err
