# aclnnIm2colBackward

[📄 查看源码](https://gitcode.com/cann/ops-cv/tree/master/image/col2im)

## 产品支持情况

| 产品                                                         | 是否支持 |
| :----------------------------------------------------------- | :------: |
| Atlas 350 加速卡                             |    √     |
| <term>Atlas A3 训练系列产品/Atlas A3 推理系列产品</term>     |    √     |
| <term>Atlas A2 训练系列产品/Atlas A2 推理系列产品</term> |    √     |
| <term>Atlas 200I/500 A2 推理产品</term>                      |    ×     |
| <term>Atlas 推理系列产品</term>                             |    ×     |
| <term>Atlas 训练系列产品</term>                              |    ×     |

## 功能说明

- 接口功能：从批处理输入张量中提取滑动局部块，将滑动局部块数组合并为一个大张量。
- 计算公式：
  
  考虑一个形状为 $(N,C,*)$的批处理input张量，其中$N$是批处理维度，$C$是通道维度，而$*$表示任意空间维度。

  此操作将input空间维度内的每个滑动kernel_size大小的块展平为形状是$(N,C×\prod(kernel\_size),L)$ 的 3-D output张量的列（即最后一维）。

  其中：
  - $C×\prod(kernel\_size)$ 是每个块内的值的数量（一个块有$\prod(kernel\_size)$ 个空间位置，每个空间位置都包含一个$C$ 通道向量），而$L$是这些块的总数：

    $$
    L=\prod_d⌊{\frac{spatial\_size[d]+2×padding[d]−dilation[d]×(kernel\_size[d]−1)−1}{stride[d]}+1}⌋
    $$

  - spatial_size由input(上面的$*$)的空间维度构成，而$d$覆盖所有空间维度。
  因此，在最后一个维度（列维度）索引，output会给出某个块内的所有值。

## 函数原型

每个算子分为[两段式接口](../common/两段式接口.md)，必须先调用“aclnnIm2colBackwardGetWorkspaceSize”接口获取计算所需workspace大小以及包含了算子计算流程的执行器，再调用“aclnnIm2colBackward”接口执行计算。

```Cpp
aclnnStatus aclnnIm2colBackwardGetWorkspaceSize(
  const aclTensor   *gradOutput, 
  const aclIntArray *inputSize, 
  const aclIntArray *kernelSize, 
  const aclIntArray *dilation, 
  const aclIntArray *padding, 
  const aclIntArray *stride, 
  aclTensor         *out, 
  uint64_t          *workspaceSize, 
  aclOpExecutor    **executor)
```

```Cpp
aclnnStatus aclnnIm2colBackward(
  void          *workspace, 
  uint64_t       workspaceSize, 
  aclOpExecutor *executor, 
  aclrtStream    stream)
```

## aclnnIm2colBackwardGetWorkspaceSize

- **参数说明**
  
  <table style="undefined;table-layout: fixed; width: 1546px"><colgroup>
  <col style="width: 165px">
  <col style="width: 121px">
  <col style="width: 325px">
  <col style="width: 272px">
  <col style="width: 252px">
  <col style="width: 121px">
  <col style="width: 149px">
  <col style="width: 141px">
  </colgroup>
  <thead>
    <tr>
      <th>参数名</th>
      <th>输入/输出</th>
      <th>描述</th>
      <th>使用说明</th>
      <th>数据类型</th>
      <th>数据格式</th>
      <th>维度(shape)</th>
      <th>非连续tensor</th>
    </tr></thead>
  <tbody>
    <tr>
      <td>gradOutput</td>
      <td>输入</td>
      <td>输入tensor。</td>
      <td>shape为(C×kernel_size[0]×kernel_size[1],L)或(N,C×kernel_size[0]×kernel_size[1],L)。</td>
      <td>BFLOAT16、FLOAT16、FLOAT</td>
      <td>ND</td>
      <td>支持2维和3维</td>
      <td>√</td>
    </tr>
    <tr>
      <td>inputSize</td>
      <td>输入</td>
      <td>输入张量的形状。</td>
      <td>值大于0。</td>
      <td>INT64</td>
      <td>-</td>
      <td>-</td>
      <td>-</td>
    </tr>
    <tr>
      <td>kernelSize</td>
      <td>输入</td>
      <td>卷积核的大小。</td>
      <td>值大于0。</td>
      <td>INT64</td>
      <td>-</td>
      <td>-</td>
      <td>-</td>
    </tr>
    <tr>
      <td>dilation</td>
      <td>输入</td>
      <td>膨胀参数。</td>
      <td>值大于0。</td>
      <td>INT64</td>
      <td>-</td>
      <td>-</td>
      <td>-</td>
    </tr>
    <tr>
      <td>padding</td>
      <td>输入</td>
      <td>卷积的填充大小。</td>
      <td>值大于等于0。</td>
      <td>INT64</td>
      <td>-</td>
      <td>-</td>
      <td>-</td>
    </tr>
    <tr>
      <td>stride</td>
      <td>输入</td>
      <td>卷积的步长。</td>
      <td>值大于0。</td>
      <td>INT64</td>
      <td>-</td>
      <td>-</td>
      <td>-</td>
    </tr>
    <tr>
      <td>out</td>
      <td>输出</td>
      <td>输出tensor。</td>
      <td>shape为(C,spatial_size[0],spatial_size[1])或(N,C,spatial_size[0],spatial_size[1])。</td>
      <td>BFLOAT16、FLOAT16、FLOAT</td>
      <td>ND</td>
      <td>支持3维和4维，且维度比gradOutput的大1。</td>
      <td>√</td>
    </tr>
    <tr>
      <td>workspaceSize</td>
      <td>输出</td>
      <td>返回用户需要在Device侧申请的workspace大小。</td>
      <td>-</td>
      <td>-</td>
      <td>-</td>
      <td>-</td>
      <td>-</td>
    </tr>
    <tr>
      <td>executor</td>
      <td>输出</td>
      <td>返回op执行器，包含了算子计算流程。</td>
      <td>-</td>
      <td>-</td>
      <td>-</td>
      <td>-</td>
      <td>-</td>
    </tr>
  </tbody></table>

- **返回值**
  
  aclnnStatus：返回状态码，具体参见[aclnn返回码](../common/aclnn返回码_cv.md)。
  
  第一段接口完成入参校验，出现以下场景时报错：

  <table style="undefined;table-layout: fixed; width: 1124px"><colgroup>
  <col style="width: 284px">
  <col style="width: 124px">
  <col style="width: 716px">
  </colgroup>
  <thead>
    <tr>
      <th>返回值</th>
      <th>错误码</th>
      <th>描述</th>
    </tr></thead>
  <tbody>
    <tr>
      <td>ACLNN_ERR_PARAM_NULLPTR</td>
      <td>161001</td>
      <td>传入的gradOutput、inputSize、kernelSize、dilation、padding、stride或out是空指针。</td>
    </tr>
    <tr>
      <td rowspan="8">ACLNN_ERR_PARAM_INVALID</td>
      <td rowspan="8">161002</td>
      <td>gradOutput的数据类型不在支持的范围之内。</td>
    </tr>
    <tr>
      <td>gradOutput的维度不是2维且不是3维。</td>
    </tr>
    <tr>
      <td>gradOutput是2维时，out不是3维；gradOutput是3维时，out不是4维。</td>
    </tr>
    <tr>
      <td>inputSize、kernelSize、dilation、padding或stride的size不为2。</td>
    </tr>
    <tr>
      <td>kernelSize、dilation或stride存在值等于或小于0的元素。</td>
    </tr>
    <tr>
      <td>padding存在值小于0的元素。</td>
    </tr>
    <tr>
      <td>gradOutput的shape不符合参数说明中的shape。</td>
    </tr>
    <tr>
      <td>out的shape不符合参数说明中的shape。</td>
    </tr>
  </tbody>
  </table>

## aclnnIm2colBackward

- **参数说明**
  
  <table style="undefined;table-layout: fixed; width: 1149px"><colgroup>
  <col style="width: 180px">
  <col style="width: 130px">
  <col style="width: 839px">
  </colgroup>
  <thead>
    <tr>
      <th>参数名</th>
      <th>输入/输出</th>
      <th>描述</th>
    </tr></thead>
  <tbody>
    <tr>
      <td>workspace</td>
      <td>输入</td>
      <td>在 Device 侧申请的 workspace 内存地址。</td>
    </tr>
    <tr>
      <td>workspaceSize</td>
      <td>输入</td>
      <td>在Device侧申请的workspace大小，由第一段接口aclnnIm2colBackwardGetWorkspaceSize获取。</td>
    </tr>
    <tr>
      <td>executor</td>
      <td>输入</td>
      <td>op 执行器，包含了算子计算流程。</td>
    </tr>
    <tr>
      <td>stream</td>
      <td>输入</td>
      <td>指定执行任务的 Stream。</td>
    </tr>
  </tbody>
  </table>

- **返回值**
  
  aclnnStatus：返回状态码，具体参见[aclnn返回码](../common/aclnn返回码_cv.md)。

## 约束说明

无。

## 调用示例

示例代码如下，仅供参考，具体编译和执行过程请参考[编译与运行样例](../common/编译与运行样例_cv.md)。

```Cpp
#include <iostream>
#include <vector>
#include "acl/acl.h"
#include "aclnnop/aclnn_im2col_backward.h"

#define CHECK_RET(cond, return_expr) \
  do {                               \
    if (!(cond)) {                   \
      return_expr;                   \
    }                                \
  } while (0)

#define LOG_PRINT(message, ...)     \
  do {                              \
    printf(message, ##__VA_ARGS__); \
  } while (0)

int64_t GetShapeSize(const std::vector<int64_t>& shape) {
  int64_t shapeSize = 1;
  for (auto i : shape) {
    shapeSize *= i;
  }
  return shapeSize;
}

int Init(int32_t deviceId, aclrtStream* stream) {
  // 固定写法，资源初始化
  auto ret = aclInit(nullptr);
  CHECK_RET(ret == ACL_SUCCESS, LOG_PRINT("aclInit failed. ERROR: %d\n", ret); return ret);
  ret = aclrtSetDevice(deviceId);
  CHECK_RET(ret == ACL_SUCCESS, LOG_PRINT("aclrtSetDevice failed. ERROR: %d\n", ret); return ret);
  ret = aclrtCreateStream(stream);
  CHECK_RET(ret == ACL_SUCCESS, LOG_PRINT("aclrtCreateStream failed. ERROR: %d\n", ret); return ret);
  return 0;
}

template <typename T>
int CreateAclTensor(const std::vector<T>& hostData, const std::vector<int64_t>& shape, void** deviceAddr,
                    aclDataType dataType, aclTensor** tensor) {
  auto size = GetShapeSize(shape) * sizeof(T);
  // 调用aclrtMalloc申请device侧内存
  auto ret = aclrtMalloc(deviceAddr, size, ACL_MEM_MALLOC_HUGE_FIRST);
  CHECK_RET(ret == ACL_SUCCESS, LOG_PRINT("aclrtMalloc failed. ERROR: %d\n", ret); return ret);
  // 调用aclrtMemcpy将host侧数据拷贝到device侧内存上
  ret = aclrtMemcpy(*deviceAddr, size, hostData.data(), size, ACL_MEMCPY_HOST_TO_DEVICE);
  CHECK_RET(ret == ACL_SUCCESS, LOG_PRINT("aclrtMemcpy failed. ERROR: %d\n", ret); return ret);

  // 计算连续tensor的strides
  std::vector<int64_t> strides(shape.size(), 1);
  for (int64_t i = shape.size() - 2; i >= 0; i--) {
    strides[i] = shape[i + 1] * strides[i + 1];
  }

  // 调用aclCreateTensor接口创建aclTensor
  *tensor = aclCreateTensor(shape.data(), shape.size(), dataType, strides.data(), 0, aclFormat::ACL_FORMAT_ND,
                            shape.data(), shape.size(), *deviceAddr);
  return 0;
}

int main() {
  // 1. （固定写法）device/stream初始化，参考acl API手册
  // 根据自己的实际device填写deviceId
  int32_t deviceId = 0;
  aclrtStream stream;
  auto ret = Init(deviceId, &stream);
  CHECK_RET(ret == ACL_SUCCESS, LOG_PRINT("Init acl failed. ERROR: %d\n", ret); return ret);

  // 2. 构造输入与输出，需要根据API的接口自定义构造
  std::vector<int64_t> gradShape = {4, 2};
  std::vector<int64_t> outShape = {1, 1, 1};

  void* gradDeviceAddr = nullptr;
  void* outDeviceAddr = nullptr;
  aclTensor* grad = nullptr;
  aclIntArray* inputSize = nullptr;
  aclIntArray* kernelSize = nullptr;
  aclIntArray* dilation = nullptr;
  aclIntArray* padding = nullptr;
  aclIntArray* stride = nullptr;
  aclTensor* out = nullptr;

  std::vector<float> gradHostData = {0.1, 1.1, 2.1, 3.1, 4.1, 5.1, 6.1, 7.1};
  std::vector<int64_t> inputSizeData = {1, 1};
  std::vector<int64_t> kernelSizeData = {2, 2};
  std::vector<int64_t> dilationData = {1, 1};
  std::vector<int64_t> paddingData = {1, 1};
  std::vector<int64_t> strideData = {1, 2};
  std::vector<float> outHostData = {0.0};

  // 创建grad aclTensor
  ret = CreateAclTensor(gradHostData, gradShape, &gradDeviceAddr, aclDataType::ACL_FLOAT, &grad);
  CHECK_RET(ret == ACL_SUCCESS, return ret);
  // 创建out aclTensor
  ret = CreateAclTensor(outHostData, outShape, &outDeviceAddr, aclDataType::ACL_FLOAT, &out);
  CHECK_RET(ret == ACL_SUCCESS, return ret);
  // 创建aclIntArray
  inputSize = aclCreateIntArray(inputSizeData.data(), 2);
  CHECK_RET(inputSize != nullptr, return ret);
  kernelSize = aclCreateIntArray(kernelSizeData.data(), 2);
  CHECK_RET(kernelSize != nullptr, return ret);
  dilation = aclCreateIntArray(dilationData.data(), 2);
  CHECK_RET(dilation != nullptr, return ret);
  padding = aclCreateIntArray(paddingData.data(), 2);
  CHECK_RET(padding != nullptr, return ret);
  stride = aclCreateIntArray(strideData.data(), 2);
  CHECK_RET(stride != nullptr, return ret);

  // 3. 调用CANN算子库API，需要修改为具体的Api名称
  uint64_t workspaceSize = 0;
  aclOpExecutor* executor;
  // 调用aclnnIm2colBackward第一段接口
  ret = aclnnIm2colBackwardGetWorkspaceSize(grad, inputSize, kernelSize, dilation, padding, stride, out,
                                            &workspaceSize, &executor);
  CHECK_RET(ret == ACL_SUCCESS, LOG_PRINT("aclnnIm2colBackwardGetWorkspaceSize failed. ERROR: %d\n", ret); return ret);
  // 根据第一段接口计算出的workspaceSize申请device内存
  void* workspaceAddr = nullptr;
  if (workspaceSize > 0) {
    ret = aclrtMalloc(&workspaceAddr, workspaceSize, ACL_MEM_MALLOC_HUGE_FIRST);
    CHECK_RET(ret == ACL_SUCCESS, LOG_PRINT("allocate workspace failed. ERROR: %d\n", ret); return ret);
  }
  // 调用aclnnIm2colBackward第二段接口
  ret = aclnnIm2colBackward(workspaceAddr, workspaceSize, executor, stream);
  CHECK_RET(ret == ACL_SUCCESS, LOG_PRINT("aclnnIm2colBackward failed. ERROR: %d\n", ret); return ret);

  // 4. （固定写法）同步等待任务执行结束
  ret = aclrtSynchronizeStream(stream);
  CHECK_RET(ret == ACL_SUCCESS, LOG_PRINT("aclrtSynchronizeStream failed. ERROR: %d\n", ret); return ret);

  // 5. 获取输出的值，将device侧内存上的结果拷贝至host侧，需要根据具体API的接口定义修改
  auto size = GetShapeSize(outShape);
  std::vector<float> resultData(size, 0);
  ret = aclrtMemcpy(resultData.data(), resultData.size() * sizeof(resultData[0]), outDeviceAddr,
                    size * sizeof(resultData[0]), ACL_MEMCPY_DEVICE_TO_HOST);
  CHECK_RET(ret == ACL_SUCCESS, LOG_PRINT("copy result from device to host failed. ERROR: %d\n", ret); return ret);
  for (int64_t i = 0; i < size; i++) {
    LOG_PRINT("result[%ld] is: %f\n", i, resultData[i]);
  }

  // 6. 释放aclTensor和aclIntArray，需要根据具体API的接口定义修改
  aclDestroyTensor(grad);
  aclDestroyIntArray(inputSize);
  aclDestroyIntArray(kernelSize);
  aclDestroyIntArray(dilation);
  aclDestroyIntArray(padding);
  aclDestroyIntArray(stride);
  aclDestroyTensor(out);

  // 7. 释放device资源，需要根据具体API的接口定义修改
  aclrtFree(gradDeviceAddr);
  aclrtFree(outDeviceAddr);
  if (workspaceSize > 0) {
    aclrtFree(workspaceAddr);
  }
  aclrtDestroyStream(stream);
  aclrtResetDevice(deviceId);
  aclFinalize();
  return 0;
}

```
