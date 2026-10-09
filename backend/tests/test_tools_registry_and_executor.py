import pytest
from app.services.tools.base import BaseTool, ToolDefinition, ToolRiskLevel
from app.services.tools.registry import ToolRegistry, default_tool_registry
from app.services.tools.executor import ToolExecutor, ToolConfirmationRequired
from app.services.tools.schemas import ToolCall, ToolResult
from app.services.tools.builtins.time_tool import GetCurrentTimeTool
from app.services.tools.builtins.calculator_tool import CalculateTool
from app.services.tools.builtins.reminder_tool import CreateTestReminderTool


class DummyTool(BaseTool):
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="dummy_tool",
            description="A test dummy tool",
            parameters_schema={
                "type": "object",
                "properties": {
                    "text": {"type": "string"}
                },
                "required": ["text"]
            },
            risk_level=ToolRiskLevel.LOW,
            requires_confirmation=False
        )

    async def execute(self, params):
        return {"echo": params.get("text")}


# MARK: - Registry Tests

def test_tool_registry_registration():
    registry = ToolRegistry()
    dummy = DummyTool()
    registry.register(dummy)

    assert registry.get("dummy_tool") is dummy
    assert len(registry.list_tools()) == 1
    assert registry.get("non_existent") is None

    definitions = registry.get_definitions()
    assert len(definitions) == 1
    assert definitions[0].name == "dummy_tool"

    unreg = registry.unregister("dummy_tool")
    assert unreg is True
    assert registry.get("dummy_tool") is None
    assert len(registry.list_tools()) == 0


def test_default_tool_registry_builtins():
    tools = default_tool_registry.list_tools()
    names = [t.definition.name for t in tools]
    assert "get_current_time" in names
    assert "calculate" in names
    assert "create_test_reminder" in names


# MARK: - Built-in Tools Tests

@pytest.mark.asyncio
async def test_get_current_time_tool():
    tool = GetCurrentTimeTool()
    assert tool.definition.risk_level == ToolRiskLevel.LOW
    assert tool.definition.requires_confirmation is False

    res = await tool.execute({"timezone": "UTC"})
    assert "datetime" in res
    assert "timezone" in res
    assert "UTC" in res["timezone"]


@pytest.mark.asyncio
async def test_calculate_tool_success():
    tool = CalculateTool()
    assert tool.definition.risk_level == ToolRiskLevel.LOW
    assert tool.definition.requires_confirmation is False

    res1 = await tool.execute({"expression": "10 + 5 * 2"})
    assert res1["status"] == "success"
    assert res1["result"] == 20

    res2 = await tool.execute({"expression": "(100 - 25) / 5"})
    assert res2["status"] == "success"
    assert res2["result"] == 15

    res3 = await tool.execute({"expression": "2 ** 8"})
    assert res3["status"] == "success"
    assert res3["result"] == 256


@pytest.mark.asyncio
async def test_calculate_tool_security_and_safety():
    tool = CalculateTool()

    # Disallow function calls
    res1 = await tool.execute({"expression": "print('hello')"})
    assert res1["status"] == "error"
    assert "Disallowed" in res1["error"]

    # Disallow arbitrary builtins / imports
    res2 = await tool.execute({"expression": "__import__('os').system('ls')"})
    assert res2["status"] == "error"
    assert "Disallowed" in res2["error"]

    # Disallow variable access
    res3 = await tool.execute({"expression": "x + 5"})
    assert res3["status"] == "error"
    assert "Disallowed" in res3["error"]

    # Prevent division by zero
    res4 = await tool.execute({"expression": "10 / 0"})
    assert res4["status"] == "error"
    assert "zero" in res4["error"].lower()

    # Prevent huge exponents
    res5 = await tool.execute({"expression": "9 ** 99999"})
    assert res5["status"] == "error"
    assert "too large" in res5["error"].lower()


@pytest.mark.asyncio
async def test_create_test_reminder_tool():
    tool = CreateTestReminderTool()
    assert tool.definition.risk_level == ToolRiskLevel.MEDIUM
    assert tool.definition.requires_confirmation is True

    res = await tool.execute({"title": "Doctor Appointment", "datetime": "tomorrow 10am"})
    assert res["status"] == "scheduled"
    assert "rem_" in res["reminder_id"]
    assert res["title"] == "Doctor Appointment"


# MARK: - Tool Executor Tests

@pytest.mark.asyncio
async def test_executor_argument_validation():
    registry = ToolRegistry()
    registry.register(DummyTool())
    executor = ToolExecutor(registry=registry)

    # Missing required argument
    res = await executor.execute(ToolCall(name="dummy_tool", arguments={}))
    assert res.success is False
    assert "Missing required parameter" in res.error

    # Valid argument
    res2 = await executor.execute(ToolCall(name="dummy_tool", arguments={"text": "hello"}))
    assert res2.success is True
    assert res2.output["echo"] == "hello"


@pytest.mark.asyncio
async def test_executor_unknown_tool():
    registry = ToolRegistry()
    executor = ToolExecutor(registry=registry)

    res = await executor.execute(ToolCall(name="ghost_tool", arguments={}))
    assert res.success is False
    assert "not registered" in res.error


@pytest.mark.asyncio
async def test_executor_enforces_confirmation():
    registry = ToolRegistry()
    registry.register(CreateTestReminderTool())
    executor = ToolExecutor(registry=registry)

    call = ToolCall(name="create_test_reminder", arguments={"title": "Test", "datetime": "tomorrow"})

    # Must raise ToolConfirmationRequired if not pre-approved
    with pytest.raises(ToolConfirmationRequired) as exc_info:
        await executor.execute(call, is_pre_approved=False)

    assert exc_info.value.pending.tool_call.name == "create_test_reminder"
    assert exc_info.value.pending.risk_level == ToolRiskLevel.MEDIUM

    # Once pre-approved, executes successfully
    res = await executor.execute(call, is_pre_approved=True)
    assert res.success is True
    assert res.output["status"] == "scheduled"
