from __future__ import annotations
import ast, math

class FormulaError(Exception):
    def __init__(self,code,message,details=None):
        self.code=code;self.message=message;self.details=details or {};super().__init__(message)

_ALLOWED_BIN=(ast.Add,ast.Sub,ast.Mult,ast.Div,ast.Pow,ast.Mod)
_ALLOWED_UNARY=(ast.UAdd,ast.USub)

def validate_formula(expression:str):
    if not expression or not expression.strip():raise FormulaError("EMPTY_FORMULA","Formula expression is required.")
    try:tree=ast.parse(expression,mode="eval")
    except SyntaxError as e:raise FormulaError("INVALID_FORMULA","Formula syntax is invalid.",{"error":str(e)}) from e
    deps=set()
    def walk(node):
        if isinstance(node,ast.Expression):walk(node.body)
        elif isinstance(node,ast.BinOp):
            if not isinstance(node.op,_ALLOWED_BIN):raise FormulaError("UNSAFE_FORMULA","Operator is not allowed.")
            walk(node.left);walk(node.right)
        elif isinstance(node,ast.UnaryOp):
            if not isinstance(node.op,_ALLOWED_UNARY):raise FormulaError("UNSAFE_FORMULA","Unary operator is not allowed.")
            walk(node.operand)
        elif isinstance(node,ast.Name):
            if node.id.startswith("_"):raise FormulaError("UNSAFE_FORMULA","Private names are not allowed.")
            deps.add(node.id)
        elif isinstance(node,ast.Constant):
            if not isinstance(node.value,(int,float)):raise FormulaError("UNSAFE_FORMULA","Only numeric constants are allowed.")
        else:
            raise FormulaError("UNSAFE_FORMULA","Formula contains unsupported syntax.",{"node":type(node).__name__})
    walk(tree)
    return {"expression":expression,"dependencies":sorted(deps)}

def evaluate_formula(expression:str,values:dict[str,float]):
    info=validate_formula(expression)
    missing=[x for x in info["dependencies"] if x not in values]
    if missing:raise FormulaError("MISSING_COMPONENT","Formula values are missing.",{"components":missing})
    try:
        result=eval(compile(ast.parse(expression,mode="eval"),"<kpi>","eval"),{"__builtins__":{}},{k:float(v) for k,v in values.items()})
    except ZeroDivisionError as e:raise FormulaError("DIVISION_BY_ZERO","Formula attempted division by zero.") from e
    except Exception as e:raise FormulaError("EVALUATION_FAILED","Formula evaluation failed.",{"error":str(e)}) from e
    result=float(result)
    if not math.isfinite(result):raise FormulaError("NON_FINITE_RESULT","Formula produced a non-finite result.")
    return {"value":result,"dependencies":info["dependencies"]}

BUILT_INS=[
 {"slug":"gross_margin_pct","name":"Gross Margin %","expression":"(revenue - cost) / revenue * 100","unit":"percent","category":"finance"},
 {"slug":"conversion_rate","name":"Conversion Rate","expression":"conversions / visitors * 100","unit":"percent","category":"marketing"},
 {"slug":"average_order_value","name":"Average Order Value","expression":"revenue / orders","unit":"currency","category":"sales"},
 {"slug":"churn_rate","name":"Churn Rate","expression":"churned_customers / starting_customers * 100","unit":"percent","category":"customer"},
 {"slug":"inventory_turnover","name":"Inventory Turnover","expression":"cost_of_goods_sold / average_inventory","unit":"ratio","category":"operations"},
]
