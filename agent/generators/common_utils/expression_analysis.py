import ast
import io
import re
import tokenize
from typing import List

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
    def validate_expression(expr: str) -> bool:
        try:
            ast.parse(expr, mode='eval')
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
