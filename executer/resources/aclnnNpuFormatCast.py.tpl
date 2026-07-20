import ctypes
import torch
import atk.tasks.backends.lib_interface.acl_wrapper as acl_wrapper
import torch.nn.functional as F
import re
from atk.common.log import Logger
from atk.tasks.backends.lib_interface.acl_wrapper import Int64, Uint64, AclTensorStruct, TORCH_TO_ACLTYPE, nnopbase, AclFormat, aclnn, AclnnStatus, TensorPtr ,AclTensorlistStruct
from atk.configs.dataset_config import InputDataset
from atk.tasks.api_execute import register
from atk.tasks.api_execute.base_api import BaseApi
from atk.tasks.api_execute.aclnn_base_api import AclnnBaseApi
from atk.tasks.backends.lib_interface.acl_wrapper import *

logging = Logger().get_logger()

@register("function")
class Function(BaseApi):
    """Auto-generated CPU reference class for aclnnNpuFormatCast."""

    _OP_NAME = "aclnnNpuFormatCast"
    _SIG_STR = """aclnnStatus aclnnNpuFormatCastGetWorkspaceSize(const aclTensor* srcTensor, aclTensor* dstTensor, uint64_t* workspaceSize, aclOpExecutor** executor)"""
    _INPUT_PARAM_NAMES = ['srcTensor']

    def __call__(self, input_data: InputDataset, with_output: bool = False):
        # Build name->value mapping from input_data.args by TOP-LEVEL input position.
        _param_map = {}
        # 按顶层 input 位置对齐:每个顶层 input 占 input_data.args 一个条目。
        # 不能 flatten:grouped attrs(aclIntArray,如 padding/normalizedShape)在 args
        # 里是单个 list 条目,flatten_list 会把它拆成 N 条与 args 逐位 zip,
        # 导致其后所有 tensor/scalar/attr 错位(位移 = 组大小 - 1)。
        if hasattr(self, 'task_result') and getattr(self.task_result.case_config, 'inputs', None) is not None:
            _ai = 0
            for _cfg in self.task_result.case_config.inputs:
                if _ai >= len(input_data.args):
                    break
                _val = input_data.args[_ai]
                _ai += 1
                if isinstance(_cfg, list):
                    # grouped input (attrs/attr_tuple/tensors): 一个 args 条目承载整个 list/tuple
                    _name = _cfg[0].name if _cfg and getattr(_cfg[0], 'name', None) else None
                else:
                    _name = getattr(_cfg, 'name', None)
                if _name:
                    _param_map[_name] = _val
                    # 同时用小写键索引,便于大小写不敏感查找
                    _param_map[_name.lower()] = _val
        # Fallback: also index kwargs if present (for non-ACLNN paths)
        if input_data.kwargs:
            for k, v in input_data.kwargs.items():
                if v is not None:
                    _param_map[k] = v
                    _param_map[k.lower()] = v

        # CPU 侧 attr 兜底(镜像 NPU 侧 handle_attr_param):直接从 case_config.inputs
        # 的 range_values 解析 attr 类参数,确保取值来自 JSON、不依赖 input_data.args
        # 的对齐——避免 args 缺失 / range_values="default" / 错位时
        # _get_param("xxx") 返回 None。标量 attr(int/double/bool)→ Python 标量;
        # grouped attrs(aclIntArray 等)→ Python list,与 NPU create_x_list 对齐。
        _cc_inputs = getattr(getattr(self, 'task_result', None), 'case_config', None)
        _cc_inputs = getattr(_cc_inputs, 'inputs', None) if _cc_inputs is not None else None
        if _cc_inputs is not None:
            def _attr_scalar(rv):
                # 与 NPU handle_attr_param 一致:list 取首元素,标量原样返回;
                # "default"/None → None(交给 _get_param 的 default 兜底)
                if rv == "default":
                    return None
                return rv[0] if isinstance(rv, list) and len(rv) > 0 else rv
            for _cfg in _cc_inputs:
                if isinstance(_cfg, list):
                    # grouped attrs / attr_tuple(aclIntArray 等):收集每项 range_values 为 list
                    _name = getattr(_cfg[0], 'name', None) if _cfg else None
                    if not _name:
                        continue
                    _vals = [_attr_scalar(getattr(_ci, 'range_values', None)) for _ci in _cfg]
                    # 全部为 None(如各项 range_values="default")→ 置 None,让 _get_param 的 default 兜底
                    if all(_v is None for _v in _vals):
                        _vals = None
                    _param_map[_name] = _vals
                    _param_map[_name.lower()] = _vals
                else:
                    _name = getattr(_cfg, 'name', None)
                    _cfg_type = getattr(_cfg, 'type', None)
                    if _name and _cfg_type in ('attr', 'attrs', 'attr_tuple'):
                        _val = _attr_scalar(getattr(_cfg, 'range_values', None))
                        _param_map[_name] = _val
                        _param_map[_name.lower()] = _val

        def _get_param(name, default=None):
            v = _param_map.get(name)
            if v is None:
                v = _param_map.get(name.lower())
            if v is not None:
                return v
            return default

        # Tensor validation: ensures a param is actually a torch.Tensor
        # ATK may sometimes generate non-tensor values (e.g. int) for params
        # expected to be tensors, causing AttributeError during computation
        def _get_tensor(name, default=None):
            v = _get_param(name, default)
            return v if isinstance(v, torch.Tensor) else default

        # ACLNN operator: aclnnNpuFormatCast
        # C++ signature: aclnnStatus aclnnNpuFormatCastGetWorkspaceSize(const aclTensor* srcTensor, aclTensor* dstTensor, uint64_t* workspaceSize, aclOpExecutor** executor)

        # --- Input parameters from signature ---
        # input tensor srcTensor
        # TODO: CPU_GOLDEN - Replace ONLY the dummy computation below with
        # the real PyTorch CPU equivalent. The parameter extraction above is pre-
        # generated by generator.py and bound to local variables of the same
        # names — keep it, do not rewrite it. Use the signature above to derive
        # the correct torch.* call. attr params (int/float/bool/str/list) are
        # already clean Python values via _get_param; tensors via _get_tensor.
        # _get_tensor validates isinstance(v, torch.Tensor) to prevent
        # AttributeError when ATK generates wrong types for tensor params.

        # Extract input tensors (use _get_tensor to validate type):
        srcTensor = _get_tensor("srcTensor")

        # [FALLBACK] Dummy output - replace with real computation above
        tensors = [a for a in input_data.args if isinstance(a, torch.Tensor)]
        if not tensors:
            tensors = [v for v in input_data.kwargs.values() if isinstance(v, torch.Tensor)]
        dtype = next((v.dtype for v in tensors), torch.float32)
        outputs = []
        def _dummy_output(out_name):
            candidates = [out_name]
            for suffix in ("Out", "Optional"):
                if out_name.endswith(suffix):
                    stripped = out_name[:-len(suffix)]
                    candidates.append(stripped)
                    if stripped.startswith("grad"):
                        candidates.append(stripped[4:])
            if out_name.startswith("grad"):
                candidates.append(out_name[4:])
            for t in tensors:
                tn = getattr(t, '_name', '')
                for k in candidates:
                    if tn and (tn == k or tn.lower() == k.lower()):
                        return torch.ones(t.shape, dtype=dtype)
            if tensors:
                return torch.ones(tensors[0].shape, dtype=dtype)
            return torch.ones([1], dtype=dtype)
        outputs.append(_dummy_output("dstTensor"))
        return outputs[0]
        # END_CPU_GOLDEN


@register("aclnn_function")
class AclnnNpuFormatCast(AclnnBaseApi):
    def init_by_input_data(self, input_data: InputDataset):
        """
        初始化输入参数并整合算子输出到输入参数列表

        处理流程：
        1. 转换输入参数为aclnn所需的c++格式
        2. 收集算子输出元数据
        3. 将算子输出张量地址追加到输入参数

        核心参数：
        1. input_args：算子的入参列表，应严格符合算子的c++函数接口原型的参数顺序和类型，均应转换为ctypes式或AclTensorStruct(List[ctypes | AclTensorStruct])
        2. output_packages：算子的出参数据包列表，用于精度对比在调用算子后解析数据包以获取算子输出(List[AclTensorStruct])
        3. tensor数据包数据结构：
            class AclTensorStruct:
                data: AclTensor       # aclTensor的c++对象（真正传递给算子的直接参数）
                addr: int             # 内存地址（用于PyTorch转换时的指针操作）
                shape: List[int]      # 张量形状
                dtype: AclDataType    # 数据类型
        """
        input_tmp={}
        input_args = []  # 算子的入参列表
        output_packages = []  # 算子的出参数据包列表

        # 获取到算子参数的入参
        param_list = self.get_param_names_excluding_last_two(self.get_cpp_func_signature_type())
        # 获取到算子参数的类型
        param_type = self.parse_operator_params(self.get_cpp_func_signature_type())

        if self.is_comm_op(self.get_cpp_func_signature_type()):
            self.handle_comm_param(input_data)

        self.handle_special_param(self.get_cpp_func_signature_type(), input_data)

        self.handle_attr_param(input_tmp, param_list)

        # === 处理输入参数 ===
        # 将输入数据转换为aclnn所需的c++格式
        for i, arg in enumerate(input_data.args):
            data = self.backend.convert_input_data(arg, index=i)
            input_args.extend(data)
        for name, kwarg in input_data.kwargs.items():
            if name in input_tmp:
                continue
            dtype = self.get_dtype_of_json(name)
            data = self.backend.convert_input_data(kwarg, name=name, dtype=dtype)
            if name in param_list:
                input_tmp[name] = data

        acl_wrapper.aclnn.bind_function(
            "aclnnNpuFormatCastCalculateSizeAndFormat",
            [
                TensorPtr,
                ctypes.c_int,  # dstFormat
                ctypes.c_int,  # additionalDtype
                ctypes.POINTER(ctypes.POINTER(ctypes.c_int64)),  # int64_t **dstShape
                ctypes.POINTER(ctypes.c_uint64),  # uint64_t *dstShapeSize
                ctypes.POINTER(ctypes.c_int),  # int *actualFormat
            ],
            AclnnStatus
        )

        dst_shape_p = ctypes.POINTER(ctypes.c_int64)()  # int64_t*
        dst_shape_size = ctypes.c_uint64(0)
        actual_format = ctypes.c_int(0)

        src = input_data.kwargs["srcTensor"]
        srcTensor = input_tmp['srcTensor'][0]

        dst_format = self.get_config_by_name(self.task_result.case_config.inputs, "dstFormat").range_values
        add_dtype = self.get_config_by_name(self.task_result.case_config.inputs, "additionalDtype").range_values

        found = self.get_config_by_name(self.task_result.case_config.inputs, "srcTensor")
        logging.info(f"found.format===========>>>>>>>>{found.format}")
        logging.info(f"dst_format===========>>>>>>>>{dst_format}")

        acl_wrapper.aclnn.aclnnNpuFormatCastCalculateSizeAndFormat(
            srcTensor.tensor,
            ctypes.c_int(dst_format),
            ctypes.c_int(add_dtype),
            ctypes.byref(dst_shape_p),
            ctypes.byref(dst_shape_size),
            ctypes.byref(actual_format)
        )

        shape_len = int(dst_shape_size.value)
        if shape_len <= 0 or not bool(dst_shape_p):
            raise RuntimeError("aclnnNpuFormatCastCalculateSizeAndFormat returned empty dstShape")

        dst_shape = tuple(int(dst_shape_p[i]) for i in range(shape_len))

        # 4) 目标 dtype：直接沿用 srcTensor.dtype；ACL dtype 用 TORCH_TO_ACLTYPE 做映射
        dst_torch_dtype = src.dtype
        try:
            dst_acl_dtype = TORCH_TO_ACLTYPE[str(dst_torch_dtype)]
        except KeyError:
            raise ValueError(f"Unsupported torch dtype for ACL: {dst_torch_dtype}")

        # 5) 在 NPU 上按目标 shape 直接创建 torch 张量（连续）
        #    注意：这样 storage/strides 都是按 dst_shape 连续的，更符合 nnopbase.create_acl_tensor 的用法

        logging.info(f"dst_shape========>>>>>{dst_shape}")
        dst_storage = torch.empty(dst_shape, dtype=dst_torch_dtype, device="npu")

        # 6) 用 nnopbase 的封装创建 aclTensor（避免你手动算 strides/storageShape/ptr）
        dstTensorStruct: AclTensorStruct = nnopbase.create_acl_tensor(
            dst_storage, AclFormat(actual_format.value)
        )
        input_args = [srcTensor, dstTensorStruct]

        # === 处理标杆输出 ===
        # 收集算子输出，并储存根据输出中的shape和dtype信息生成的AclTensorStruct数据结构
        # 输出数据结构说明：
        for index, output_data in enumerate(self.task_result.output_info_list):
            output = self.backend.convert_output_data(output_data, index)
            output_packages.extend(output)  # 保存完整AclTensorStruct结构

        return input_args, output_packages

    def after_call(self, output_packages):
        output = []
        for output_pack in output_packages:
            if isinstance(output_pack, AclTensorStruct):
                output.append(self.acl_tensor_to_torch(output_pack))
            elif isinstance(output_pack, AclTensorlistStruct):
                output.append(self.acl_tensorlist_to_torch(output_pack))
        return output

    def get_storage_shape(self, input_data: InputDataset, index=None, name=None):
        if name is not None:
            found = self.get_config_by_name(self.task_result.case_config.inputs, name)
            if not isinstance(found, list) and found.shape is not None:
                return torch.Size(found.shape)
        return None

    def get_format(self, input_data: InputDataset, index=None, name=None):
        found = None
        if name is not None:
            found = self.get_config_by_name(self.task_result.case_config.inputs, name)
        if found is not None and not isinstance(found, list):
            format = found.format
            return self.get_acl_format(format)
        return AclFormat.ACL_FORMAT_ND

    def get_dtype_of_json(self, name=None):
        found = None
        if name is not None:
            found = self.get_config_by_name(self.task_result.case_config.inputs, name)
        if not isinstance(found, list):
            return found.dtype
        return found[0].dtype

    def get_cpp_func_signature_type(self):
        # return "aclnnStatus aclnnAlltoAllMatmulGetWorkspaceSize( const aclTensor* x1, const aclTensor* x2, const aclTensor* biasOptional, const aclIntArray* alltoAllAxesOptional, const char* group, bool transposeX1, bool transposeX2, const aclTensor* output, const aclTensor* alltoAllOutOptional, uint64_t* workspaceSize, aclOpExecutor** executor)"
        #return "aclnnStatus aclnnGroupedMatmulV5GetWorkspaceSize( const aclTensorList *x, const aclTensorList *weight, const aclTensorList *biasOptional, const aclTensorList *scaleOptional, const aclTensorList *offsetOptional, const aclTensorList *antiquantScaleOptional, const aclTensorList *antiquantOffsetOptional, const aclTensorList *perTokenScaleOptional, const aclTensor *groupListOptional, const aclTensorList *activationInputOptional, const aclTensorList *activationQuantScaleOptional, const aclTensorList *activationQuantOffsetOptional, int64_t splitItem, int64_t groupType, int64_t groupListType, int64_t actType, aclIntArray *tuningConfigOptional, aclTensorList *out, aclTensorList *activationFeatureOutOptional, aclTensorList *dynQuantScaleOutOptional, uint64_t *workspaceSize, aclOpExecutor **executor)"
        # return "aclnnStatus aclnnBatchMatMulWeightNzGetWorkspaceSize( const aclTensor *self, const aclTensor *mat2, aclTensor *out, int8_t cubeMathType, uint64_t *workspaceSize, aclOpExecutor **executor)"
        # return "aclnnStatus aclnnFFNV3GetWorkspaceSize( const aclTensor* x, const aclTensor* weight1, const aclTensor* weight2, const aclTensor* expertTokensOptional, const aclTensor* bias1Optional, const aclTensor* bias2Optional, const aclTensor* scaleOptional, const aclTensor* offsetOptional, const aclTensor* deqScale1Optional, const aclTensor* deqScale2Optional, const aclTensor* antiquantScale1Optional, const aclTensor* antiquantScale2Optional, const aclTensor* antiquantOffset1Optional, const aclTensor* antiquantOffset2Optional, const char* activation, int64_t innerPrecise, bool tokensIndexFlag, const aclTensor* y, uint64_t* workspaceSize, aclOpExecutor** executor)"
        return "aclnnStatus aclnnNpuFormatCastGetWorkspaceSize( const aclTensor* srcTensor, aclTensor* dstTensor, uint64_t* workspaceSize, aclOpExecutor** executor)"
        # return "aclnnStatus aclnnReflectionPad1dBackwardGetWorkspaceSize( const aclTensor *gradOutput, const aclTensor *self, const aclIntArray *padding, aclTensor *gradInput, uint64_t *workspaceSize, aclOpExecutor **executor)"
        # return "aclnnStatus aclnnSwinAttentionScoreQuantGetWorkspaceSize( const aclTensor *query, const aclTensor *key, const aclTensor *value, const aclTensor *scaleQuant, const aclTensor *scaleDequant1, const aclTensor *scaleDequant2, const aclTensor *biasQuantOptional, const aclTensor *biasDequant1Optional, const aclTensor *biasDequant2Optional, const aclTensor *paddingMask1Optional, const aclTensor *paddingMask2Optional, bool queryTranspose, bool keyTranspose, bool valueTranspose, int64_t softmaxAxes, const aclTensor *out, uint64_t *workspaceSize, aclOpExecutor **executor)"
        # return "aclnnStatus aclnnSwinTransformerLnQkvQuantGetWorkspaceSize( const aclTensor *x, const aclTensor *gamma, const aclTensor *beta, const aclTensor *weight, const aclTensor *bias, const aclTensor *quantScale, const aclTensor *quantOffset, const aclTensor *dequantScale, int64_t headNum, int64_t seqLength, double epsilon, int64_t oriHeight, int64_t oriWeight, int64_t WinSize, int64_t wWinSize, bool weightTranspose, const aclTensor *queryOutputOut, const aclTensor *keyOutputOut, const aclTensor *valueOutputOut, uint64_t *workspaceSize, aclOpExecutor **executor)"

    def get_param_names_excluding_last_two(self, func_str):
        """只返回参数名称列表，排除最后两个"""
        # 提取参数列表
        match = re.search(r'\(([^)]*)\)', func_str)
        if not match:
            return []

        params_str = match.group(1)

        # 提取所有参数名
        param_names = []
        for param in params_str.split(','):
            param = param.strip()
            # 提取最后的标识符作为参数名
            name_match = re.search(r'\*?\s*(\w+)\s*$', param)
            if name_match:
                param_names.append(name_match.group(1))
        # 排除最后两个
        return param_names[:-2] if len(param_names) >= 2 else param_names


    def handle_attr_param(self, input_tmp, param_list):
        for config in self.task_result.case_config.inputs:
            if not isinstance(config, list):
                if config.name not in param_list:
                    continue
                if config.type == "attr":
                    range_val = config.range_values[0] if isinstance(config.range_values, list) else config.range_values
                    ctype = self.get_ctype(config.dtype)
                    if ctype == ctypes.c_char_p:  # Python的字符串是Unicode字符串，C风格的字符串一般是字节字符串（如ASCII或UTF-8）
                        range_val = range_val.encode('utf-8')
                    input_tmp[config.name] = [ctype(range_val)]
            else:
                data = []
                data_name = config[0].name
                if data_name not in param_list:
                    continue
                for config_item in config:
                    if config_item.type == "attrs" or config_item.type == "attr_tuple":
                        range_val = config_item.range_values[0] if isinstance(config_item.range_values, list) else config_item.range_values
                        data.append(self.get_ctype(config_item.dtype)(range_val))
                        input_tmp[data_name] = nnopbase.create_x_list(data)

    def handle_special_param(self, operator_name, input_data):

        if "aclnnBatchMatMulWeightNz" in operator_name:
            found_mat2_transposed = next((config for config in self.task_result.case_config.inputs if config.name == "mat2_transposed"), None)
            # 转置的情况
            if found_mat2_transposed.range_values:
                # 转换
                input_data.kwargs['mat2'] = input_data.kwargs['mat2'].permute(0, 2, 1, 4, 3).reshape(
                    input_data.kwargs['mat2'].shape[0],  # b
                    input_data.kwargs['mat2'].shape[1] * input_data.kwargs['mat2'].shape[4],  # k1 * k0 = k
                    input_data.kwargs['mat2'].shape[2] * input_data.kwargs['mat2'].shape[3]  # n1 * n0 = n
                )
                input_data.kwargs['self'] = input_data.kwargs['self'].permute(0, 2, 1)
                return
            input_data.kwargs['mat2'] = input_data.kwargs['mat2'].reshape(
                input_data.kwargs['mat2'].shape[0],  # a
                input_data.kwargs['mat2'].shape[2] * input_data.kwargs['mat2'].shape[3],  # c*d
                input_data.kwargs['mat2'].shape[1] * input_data.kwargs['mat2'].shape[4]  # b*e
            )


    def handle_comm_param(self, input_data):
        # 处理group
        rank_id = self.dist_task_info.rank
        self.group = input_data.kwargs['group']
        input_data.kwargs['group'] = self.group._get_backend(torch.device("npu")).get_hccl_comm_name(rank_id)

    def is_comm_op(self, operator_name):
        op_name = ["AlltoAll"]
        if operator_name in op_name:
            return True
        return False

    def parse_operator_params(self, func_signature: str):
        """
        从函数签名中解析出参数名和参数类型的字典（不包含const和*）

        Args:
            func_signature: 函数签名字符串

        Returns:
            字典，key为参数名，value为参数类型（不含const和*）
        """
        # 提取函数括号内的所有参数
        params_match = re.search(r'\((.*?)\)\s*$', func_signature, re.DOTALL)
        if not params_match:
            return {}

        params_str = params_match.group(1)

        # 按逗号分割参数，但要注意嵌套的尖括号
        params = []
        current_param = ""
        angle_bracket_count = 0
        paren_count = 0

        for char in params_str:
            if char == '<':
                angle_bracket_count += 1
            elif char == '>':
                angle_bracket_count -= 1
            elif char == '(':
                paren_count += 1
            elif char == ')':
                paren_count -= 1
            elif char == ',' and angle_bracket_count == 0 and paren_count == 0:
                params.append(current_param.strip())
                current_param = ""
                continue

            current_param += char

        if current_param.strip():
            params.append(current_param.strip())

        # 解析每个参数
        result = {}
        for param in params:
            # 匹配参数类型和参数名
            match = re.match(r'^(const\s+)?(.+?)\s+(\*?\s*)([a-zA-Z_][a-zA-Z0-9_]*)(\s*=\s*.*)?$', param.strip())
            if match:
                param_type = match.group(2).strip()
                param_name = match.group(4).strip()

                # 去掉类型中的const和*（包括指针标记）
                # 先去掉const
                param_type = param_type.replace('const ', '').replace('const', '').strip()
                # 再去掉所有*
                param_type = param_type.replace('*', '').strip()

                result[param_name] = param_type

        return result

    def get_config_by_name(self, configs, target_name: str):
        """
        根据name获取配置数据

        Args:
            configs: 混合数组，元素可能是InputCaseConfig对象或List[InputCaseConfig]
            target_name: 目标name值

        Returns:
            找到的配置对象或列表，未找到返回None
        """
        for item in configs:
            if isinstance(item, list):
                # 如果是列表，检查第一个元素的name
                if item and hasattr(item[0], 'name') and item[0].name == target_name:
                    return item  # 返回整个列表
            elif hasattr(item, 'name') and item.name == target_name:
                return item  # 返回单个对象

        return None

    def get_acl_format(self, format_str):
        FORMAT_MAPPING = {
            'ND': AclFormat.ACL_FORMAT_ND,
            'NZ': AclFormat.ACL_FORMAT_FRACTAL_NZ,
            'NCHW': AclFormat.ACL_FORMAT_NCHW,
            'NC': AclFormat.ACL_FORMAT_NC,
            'HWCN': AclFormat.ACL_FORMAT_HWCN,
            'NHWC': AclFormat.ACL_FORMAT_NHWC,
            'NC1HWC0': AclFormat.ACL_FORMAT_NC1HWC0,
            'NDC1HWC0': AclFormat.ACL_FORMAT_NDC1HWC0,
            'NCL': AclFormat.ACL_FORMAT_NCL,
            'NCDHW': AclFormat.ACL_FORMAT_NCDHW,
            'NDHWC': AclFormat.ACL_FORMAT_NDHWC,
            'FRACTAL_Z_3D': AclFormat.ACL_FRACTAL_Z_3D,
        }

        if format_str in FORMAT_MAPPING:
            return FORMAT_MAPPING[format_str]
        else:
            logging.error(f"not found format: {format_str}")
            return AclFormat.ACL_FORMAT_ND

    def get_ctype(self, type_str):
        PYTYPE_TO_CTYPE = {
            "float": ctypes.c_float,
            "float32": ctypes.c_float,
            "double": ctypes.c_double,
            "int": ctypes.c_int64,
            "int4": ctypes.c_int8,
            "int8_t": ctypes.c_int8,
            "int8": ctypes.c_int8,
            "int32_t": ctypes.c_int32,
            "int32": ctypes.c_int32,
            "int64_t": ctypes.c_int64,
            "int64": ctypes.c_int64,
            "uint8_t": ctypes.c_uint8,
            "uint8": ctypes.c_uint8,
            "uint32_t": ctypes.c_uint32,
            "uint32": ctypes.c_uint32,
            "uint64_t": ctypes.c_uint64,
            "uint64": ctypes.c_uint64,
            "bool": ctypes.c_bool,
            "attr_bool": ctypes.c_bool,
            "str": ctypes.c_char_p,
            "string": ctypes.c_char_p,
        }

        if type_str in PYTYPE_TO_CTYPE:
            return PYTYPE_TO_CTYPE[type_str]
        else:
            raise ValueError(f"Unsupported CTYPE format: {type_str}")
