"""
为ACLnnGroupedMatmulV的海思csv用例数据填充groupListOptional填充值，当前在csv数据的attributes列，增加groupListData字段，以数组的模式提供groupListOptional数据的range填充值
"""
import argparse
import ast
import csv
import logging

from numpy.ma.core import remainder


def parse_tensor_shapes(shape_str):
    """
    基于正则表达式，解析tensor_view_shape字符串，返回各tensor的shape
    示例："(((1,2),),((1023, 2, 1),), None, None, None, (1023,), None, None)"
    先去掉最外层括号，再按照顶层逗号分割
    :param shape_str: 算子的tensor_view_shape
    :return: List: [((1,2),),((1023, 2, 1),), None, None, None, (1023,), None, None]
    """
    shape_list = list(ast.literal_eval(shape_str))
    if shape_list is None:
        return []
    return shape_list


def generate_group_list_data(group_list_type, group_type, M, g):
    """
    根据groupListType生成groupListData
    :param group_list_type:
    groupListType=0：须为非负单调非递减数列（累积和）。以M=256、E=4（各组大小依次为64、0、128、64）为例：[64, 64, 192, 256]
    groupListType=1：须为非负数列（各组大小）。例如：[64, 0, 128, 64]
    groupListType=2：groupList须为非负数列，shape为[E, 2]，E表示Group大小，数据排布为[[groupIdx0, groupSize0], [groupIdx1, groupSize1], ...]，其中groupSize为分组轴上每组大小，必须为非负数。所有groupSize非0的分组按groupIdx有序排列在前，所有groupSize为0的分组按groupIdx有序排列在后，确保非零组前置、零值组后置，且组内有序。例如：[[0, 64], [2, 128], [3, 64], [1, 0]]
    :param group_type: groupType, 支持M轴分组（0）和不分组（-1），非量化额外支持 K轴分组（2）。全量化下 groupType 仅支持0（M轴分组）
    :param M:
    :param g:
    :return:
    """
    if g is None or M is None:
        return None
    # 累计和模式, 非负，单调非递减
    # g = 4, M = 64 -> [16, 32, 48, 64]
    # g = 3, M = 100, -> [34, 67, 100]
    if group_list_type == 0:
        per_group = M // g
        group_remainder = M % g
        data = []
        cumsum = 0
        for j in range(g):
            gs = per_group + (1 if j < group_remainder else 0)
            cumsum += gs
            data.append(cumsum)
        return data
    # 组大小模式，简单策略，将M平均分给E个组, sum(data) <= M
    # g = 4, M = 64 -> [16, 16, 16, 16]
    # g = 8, M = 64, -> [8, 8, 8, 8, 8, 8, 8, 8]
    elif group_list_type == 1:
        per_group = M // g
        group_remainder = M % g
        data = [per_group + (1 if j < group_remainder else 0) for j in range(g)]
        return data
    # 索引 - 大小配对模式，仅groupType=0时支持
    # g = 4, M = 64 -> [[0, 16], [1, 16], [2, 16], [3, 16]]
    # g = 4, M = 64, -> [[0,32], [1, 32], [2, 0], [3, 0]]
    elif group_list_type == 2:
        if group_type != 0:
            return None
        per_group = M // g
        group_remainder = M % g
        sizes = [per_group + (1 if j < group_remainder else 0) for j in range(g)]
        data = [[i, size_i] for i, size_i in enumerate(sizes)]
        return data
    return None


def process_csv(input_file, output_file, group_list_data_attr_name="groupListData"):
    """
    读取CSV数据，填充groupListOptional的range值,并保存
    :param input_file: 输入csv文件
    :param output_file: 输出csv文件
    :param group_list_data_attr_name: csv数据中groupListOptional
    :return: None
    """
    with open(input_file, 'r', encoding="utf-8") as f:
        content = f.read()
    lines = content.strip().split('\n')

    reader = csv.DictReader(lines)
    rows = list(reader)

    output_rows = []
    for i, row in enumerate(rows):
        shapes_str = row["tensor_view_shapes"]
        attrs_str = row["attributes"]

        attrs = ast.literal_eval(attrs_str)

        tensor_shapes = parse_tensor_shapes(shapes_str)
        x_tensor_shape_index = 0
        weight_tensor_shape_index = 1
        out_tensor_shape_index = 12

        group_list_type = attrs.get("group_list_type", 1)
        group_type = attrs.get("group_type", 0)
        # 提取x tensor(索引0)和weight tensor(索引1)的shape
        x_shape = tensor_shapes[x_tensor_shape_index][0] if len(tensor_shapes) > 0 else None
        weight_shape = tensor_shapes[weight_tensor_shape_index][0] if len(tensor_shapes) > 1 else None
        out_shape = tensor_shapes[out_tensor_shape_index][0] if len(tensor_shapes) > 1 else None

        # 获取M(x tensor第一维)和g(weight tensor第一维)
        if group_type == 0:
            M = x_shape[0] if x_shape else None
            g = weight_shape[0]
        elif group_type == 2:
            M = x_shape[1] if x_shape else None
            g = out_shape[0] if out_shape else None
        else:
            logging.warning(f"groupType : {group_type}, groupListType : {group_list_type}, M : {None}, g : {None}")
            M = None
            g = None
        group_list_data = generate_group_list_data(group_list_type, group_type, M, g)
        if group_list_data is not None:
            attrs[group_list_data_attr_name] = group_list_data
            row["attributes"] = attrs
        output_rows.append(row)
    with open(output_file, "w", encoding="utf-8") as f:
        field_names = reader.fieldnames
        writer = csv.DictWriter(f, field_names)
        writer.writeheader()
        writer.writerows(output_rows)
    logging.info(f"GroupListOptional data solve end, output file : {output_file}")
    logging.info(f"Total lines : {len(output_rows)}")



if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument("--input_case_csv", type=str, required=True, help="Input case csv path")
    parser.add_argument("--output_case_csv", type=str, required=False, default=None, help="Output case csv path")
    parser.add_argument("--group_list_data_attr_name", type=str, required=False, default="groupListData",
                        help="Group list type")

    args = parser.parse_args()
    input_file = args.input_case_csv
    if args.output_case_csv is not None:
        output_file = args.output_case_csv
    else:
        output_file = input_file
    group_list_data_attr_name = args.group_list_data_attr_name
    process_csv(input_file, output_file, group_list_data_attr_name)
