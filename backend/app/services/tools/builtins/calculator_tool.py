import ast
import operator
from typing import Dict, Any

from app.services.tools.base import BaseTool, ToolDefinition, ToolRiskLevel


_SAFE_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _safe_eval_node(node: ast.AST) -> float:
    """
    Recursively evaluates an AST expression node using strictly whitelisted math operators.
    Raises ValueError on any disallowed node (e.g. calls, attributes, variables, loops).
    """
    if isinstance(node, ast.Expression):
        return _safe_eval_node(node.body)

    elif isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return float(node.value)
        raise ValueError(f"Unsupported constant type: {type(node.value).__name__}")

    elif isinstance(node, ast.UnaryOp):
        op_type = type(node.op)
        if op_type in _SAFE_OPERATORS:
            operand = _safe_eval_node(node.operand)
            return _SAFE_OPERATORS[op_type](operand)
        raise ValueError(f"Disallowed unary operator: {op_type.__name__}")

    elif isinstance(node, ast.BinOp):
        op_type = type(node.op)
        if op_type in _SAFE_OPERATORS:
            left = _safe_eval_node(node.left)
            right = _safe_eval_node(node.right)

            # Prevent exponential Denial-of-Service (e.g. 9**999999)
            if op_type is ast.Pow:
                if right > 100 or left > 10000:
                    raise ValueError("Exponent or base too large for safe evaluation.")

            if op_type in (ast.Div, ast.FloorDiv, ast.Mod) and right == 0:
                raise ValueError("Division by zero.")

            return _SAFE_OPERATORS[op_type](left, right)
        raise ValueError(f"Disallowed binary operator: {op_type.__name__}")

    raise ValueError(f"Disallowed expression node: {type(node).__name__}")


class CalculateTool(BaseTool):
    """
    Safe mathematical calculation tool.
    Parses expressions via AST to prevent arbitrary code execution.
    """

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="calculate",
            description="Evaluates a mathematical expression (e.g. '(15 * 4) + 22.5', '2 ** 8', '100 / 4'). Only pure arithmetic is supported.",
            parameters_schema={
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "The mathematical expression to evaluate."
                    }
                },
                "required": ["expression"]
            },
            risk_level=ToolRiskLevel.LOW,
            requires_confirmation=False,
        )

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        expr = str(params.get("expression", "")).strip()
        if not expr:
            return {"error": "Missing expression parameter."}

        try:
            tree = ast.parse(expr, mode="eval")
            result = _safe_eval_node(tree)

            # Format nicely as int if it's an exact whole number
            if result.is_integer():
                result_val = int(result)
            else:
                result_val = round(result, 6)

            return {
                "expression": expr,
                "result": result_val,
                "status": "success"
            }
        except Exception as exc:
            return {
                "expression": expr,
                "error": f"Calculation error: {str(exc)}",
                "status": "error"
            }
