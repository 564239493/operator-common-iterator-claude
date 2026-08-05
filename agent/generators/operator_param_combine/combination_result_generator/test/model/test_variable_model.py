import pytest

from model.variable_model import (
    VariableModel
)

class TestVariableModel:
    """
    验证：
    创建
    fullname生成
    value访问
    不可变性
    """

    def test_variable_full_name(self):

        variable = VariableModel(

            variable_id=1,

            parameter_name="x",

            attribute_name="dtype",

            values=("fp16","fp32")

        )


        assert variable.full_name == "x.dtype"



    def test_variable_values(self):

        variable = VariableModel(

            variable_id=1,

            parameter_name="x",

            attribute_name="dtype",

            values=("fp16",)

        )


        assert variable.values == ("fp16",)



    def test_variable_immutable(self):

        variable = VariableModel(

            variable_id=1,

            parameter_name="x",

            attribute_name="dtype",

            values=("fp16",)

        )


        with pytest.raises(Exception):

            variable.values=("int8",)