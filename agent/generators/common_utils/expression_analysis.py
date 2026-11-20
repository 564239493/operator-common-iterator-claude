import ast
import io
import keyword
import re
import tokenize
from typing import List, Set

from agent.generators.common_utils.logger_util import LazyLogger
from agent.generators.data_definition.constants import DataMatchMap

logger = LazyLogger()

class ExpressionPreprocessor:
    """Preprocesses expressions before solving."""

    @staticmethod
    def normalize_json_null(expr: str) -> str:
        """Convert bare JSON ``null`` tokens to Python ``None`` safely.

        Quoted string values such as ``"null"`` are intentionally unchanged.
        """
        tokens = []
        for token in tokenize.generate_tokens(io.StringIO(expr).readline):
            if token.type == tokenize.NAME and token.string == "null":
                token = tokenize.TokenInfo(
                    token.type, "None", token.start, token.end, token.line
                )
            tokens.append(token)
        return tokenize.untokenize(tokens)

    @staticmethod
    def apply_keyword_replace(expr: str) -> str:
        # expr = ExpressionPreprocessor.normalize_json_null(expr)
        for keyword, replacement in DataMatchMap.EXPR_KEYWORD_REPLACE.items():
            if replacement is None:
                expr = expr.replace(keyword, 'None')
            elif isinstance(replacement, str):
                expr = expr.replace(keyword, f"'{replacement}'")
            else:
                expr = expr.replace(keyword, str(replacement))
        for keyword, replacement in DataMatchMap.ACL_DTYPE_TRANSFER_TENSOR_MAP.items():
            replacement_str = f"{replacement}" if isinstance(replacement, str) else str(replacement)
            expr = re.sub(rf"\b{re.escape(keyword)}\b", replacement_str, expr)
        return expr

    @staticmethod
    def preprocess_expressions(expressions: List[str]) -> List[str]:
        processed = []
        for expr in expressions:
            expr = ExpressionPreprocessor.apply_keyword_replace(expr)
            processed.append(expr)
        return processed

    @staticmethod
    def keyword_param_names(param_names) -> Set[str]:
        """从参数名清单中挑出 Python 关键字形式的参数名（白名单）。

        只对真实参数名里恰为关键字的成员（如 C++ 接口的 ``from``）做转义，
        不触碰 ``and``/``or``/``not``/``True``/``None`` 等表达式内置语法关键字。
        """
        if not param_names:
            return set()
        return {
            str(name) for name in param_names
            if isinstance(name, str) and keyword.iskeyword(name)
        }

    @staticmethod
    def keyword_param_alias_map(param_names) -> dict:
        """真实参数名 → 表达式层别名（``from`` → ``from_``）。

        若别名本身也是真实参数名（同名冲突），该参数不转义，避免误伤。
        """
        names = {str(name) for name in (param_names or []) if isinstance(name, str)}
        aliases = {}
        for name in ExpressionPreprocessor.keyword_param_names(names):
            alias = f"{name}_"
            if alias not in names:
                aliases[name] = alias
        return aliases

    @staticmethod
    def escape_keyword_names(expr: str, param_names=None) -> str:
        """把表达式文本中「关键字参数名」的 NAME token 改写为 ``<name>_``。

        仅在 expr 文本层做转义（schema 与执行层仍使用真实参数名），覆盖裸引用
        （``from``）与属性访问（``from.range_value``）。非参数名的语法关键字
        不在白名单内，保持原样。
        """
        if not isinstance(expr, str) or not expr:
            return expr
        aliases = ExpressionPreprocessor.keyword_param_alias_map(param_names)
        if not aliases:
            return expr
        try:
            tokens = list(tokenize.generate_tokens(io.StringIO(expr).readline))
        except (tokenize.TokenError, IndentationError):
            return expr
        changed = False
        escaped = []
        for token in tokens:
            if token.type == tokenize.NAME and token.string in aliases:
                token = tokenize.TokenInfo(
                    token.type, aliases[token.string], token.start, token.end, token.line
                )
                changed = True
            escaped.append(token)
        if not changed:
            return expr
        return tokenize.untokenize(escaped)

    @staticmethod
    def restore_keyword_names(expr: str, param_names=None) -> str:
        """把表达式文本中的关键字参数名别名（``from_``）还原为真实名。

        供不做 AST 解析、只做文本改写/正则处理的链路使用；schema 与执行层
        始终使用真实参数名。
        """
        if not isinstance(expr, str) or not expr:
            return expr
        aliases = ExpressionPreprocessor.keyword_param_alias_map(param_names)
        if not aliases:
            return expr
        alias_to_real = {alias: real for real, alias in aliases.items()}
        try:
            tokens = list(tokenize.generate_tokens(io.StringIO(expr).readline))
        except (tokenize.TokenError, IndentationError):
            return expr
        changed = False
        restored = []
        for token in tokens:
            if token.type == tokenize.NAME and token.string in alias_to_real:
                token = tokenize.TokenInfo(
                    token.type, alias_to_real[token.string],
                    token.start, token.end, token.line,
                )
                changed = True
            restored.append(token)
        if not changed:
            return expr
        return tokenize.untokenize(restored)

    @staticmethod
    def restore_keyword_param_names(tree, param_names=None):
        """把已解析 AST 中的别名 NAME 还原为真实参数名。

        别名只用于通过 Python 语法解析；还原后 ASTtoZ3Converter / PICT 翻译 /
        eval 命名空间都能按真实参数名取值。
        """
        aliases = ExpressionPreprocessor.keyword_param_alias_map(param_names)
        if not aliases or tree is None:
            return tree
        alias_to_real = {alias: real for real, alias in aliases.items()}
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in alias_to_real:
                node.id = alias_to_real[node.id]
        return tree

    @staticmethod
    def parse_expression(expr: str, param_names=None) -> ast.Expression:
        """解析约束表达式，兼容 Python 关键字参数名。

        先做文本层别名转义，``ast.parse`` 通过后再把 AST 中的别名还原为真实
        参数名。调用方拿到的树与真实参数名（schema/执行层口径）一致。
        """
        escaped = ExpressionPreprocessor.escape_keyword_names(expr, param_names)
        tree = ast.parse(escaped, mode='eval')
        return ExpressionPreprocessor.restore_keyword_param_names(tree, param_names)

    @staticmethod
    def validate_expression(expr: str, param_names=None) -> bool:
        try:
            ast.parse(
                ExpressionPreprocessor.escape_keyword_names(expr, param_names),
                mode='eval',
            )
            return True
        except SyntaxError as e:
            logger.error(f"Expression '{expr}' is invalid by ast validation, err msg : {str(e)}")
            return False

    @staticmethod
    def validate_expression_without_bool(expr: str) -> bool:
        """
        判断expr是否为合法表达式，且本身不为True/False
        """
        try:
            tree = ast.parse(expr, mode='eval')
            # tree.body 是表达式节点
            node = tree.body

            # 布尔字面量是 ast.Constant 且值为 bool
            if isinstance(node, ast.Constant) and isinstance(node.value, bool):
                return False
            # 其他情况（包括其他类型的表达式）都认为是有效的
            return True
        except SyntaxError as e:
            # 解析失败，说明不是合法的表达式
            logger.error(f"Validate expression without bool failed, err msg : {str(e)}")
            return False
