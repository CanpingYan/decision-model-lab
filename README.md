# Decision Model Lab · 决策模型实验室

Decision Model Lab 是一个在本机浏览器里运行的决策模型评测工具。导入 Excel 后，可以查看单道题的判定过程，也可以用同一批题比较五款模型的整题正确率、请求异常和耗时。

内置三档中文题集，共 600 道合成用例，覆盖工单分流、证据判断、工具选择与记忆更新。

## 快速开始

建议使用 Python 3.12。应用服务只依赖 Python 标准库。开发测试另需 Node.js 18 或更新版本。本地模型的安装命令适用于 macOS 和 Linux。

```sh
git clone https://github.com/CanpingYan/decision-model-lab.git
cd decision-model-lab
python3 app/serve.py
```

在项目根目录运行后，访问 [http://127.0.0.1:8765/](http://127.0.0.1:8765/)。服务在本机回环地址上监听。即使未配置模型，也可以打开页面、下载和导入题集。端口冲突时，可以运行 `python3 app/serve.py 8766`，再打开相应地址。

1. 在页面顶部下载一份示例题集，或使用仓库里的 [Excel 模板](app/web/examples/测试用例模板.xlsx)准备自己的题集。
2. 点击「导入 Excel」并选择当前用例集。可以一次导入多份 `.xlsx`，每份的结果单独保存。
3. [配置一种模型](#模型配置)后，进入「场景演示」运行单题。在「对比测试」勾选模型，选择用例范围，运行并导出 JSON 或 CSV。

## 模型配置

首次配置时，在项目根目录执行以下命令。已有 `.env` 时直接编辑。配置修改后重启服务。模型可以独立配置，未使用的字段留空即可。

```sh
test -f .env || cp .env.example .env
chmod 600 .env
```

### 云端模型

| 模型 | 需要填写的字段 | 默认模型 |
| --- | --- | --- |
| [DeepSeek](https://api-docs.deepseek.com/) | `DEEPSEEK_API_KEY` | `deepseek-flash` |
| [TypeSafe JEV](https://docs.typesafe.ai/introduction) | `JEV_API_KEY` | `jev-1.14.0` |
| [阿里云百炼 Decision](https://help.aliyun.com/zh/model-studio/decision-model-api) | `ALIBABA_API_KEY`，以及 `ALIBABA_ENDPOINT` 或 `ALIBABA_WORKSPACE_ID` | `decision-model-preview` |

对阿里云百炼，`ALIBABA_ENDPOINT` 使用模型控制台提供的完整 System One HTTPS 地址。按业务空间拼接地址时，`ALIBABA_REGION` 填空间所在地域，例如北京 `cn-beijing` 或新加坡 `ap-southeast-1`。

云端端点和模型名可在 [配置模板](.env.example) 中查看和覆盖。API 密钥仅填入自己的 `.env`，自定义端点应使用可信的 HTTPS 服务。如果显示端点重定向，请填写服务商提供的最终 API 地址。模型可用性和费用以相应服务商的后台为准。

### 本地模型

两个本地模型使用独立的 Python 环境。按需要安装其中一个即可。下载脚本从原仓库取固定版本，检查权重大小和已发布的 SHA-256，文件保存在 `.runtime/models/`。

安装 [Laya Multilingual](https://huggingface.co/convaiinnovations/laya-multilingual)：

```sh
python3 -m venv .runtime/laya-venv
.runtime/laya-venv/bin/python -m pip install -r requirements-runtime.txt
.runtime/laya-venv/bin/python -m pip install --no-deps laya==0.4.35
.runtime/laya-venv/bin/python app/lab/model_download.py
```

安装 [Chinese Decision 0.8B](https://huggingface.co/quincyqiang/chinese-decision-0.8b)：

```sh
python3 -m venv .runtime/chinese-venv
.runtime/chinese-venv/bin/python -m pip install -r requirements-chinese-runtime.txt
.runtime/chinese-venv/bin/python app/lab/chinese_download.py
```

重启服务，在对应模型卡片点击「加载」。默认使用完整权重进行 FP32 推理，并尝试选择可用的 CUDA、MPS 或 CPU。遇到 GPU 兼容性问题时，可将 `LAYA_DEVICE` 或 `CHINESE_DEVICE` 设为 `cpu`。模型位置和工作进程的 Python 路径可在 `.env` 中修改。模型加载日志保存在 `.runtime/`。

初次安装依赖和下载权重需要联网。仓库仅提供适配代码，权重按各自模型仓库的许可使用。

## 题集与评分

| 题集 | 侧重点 |
| --- | --- |
| [简单 200 题](app/web/examples/中文简单用例200题.xlsx) | 单一事实、直接意图或明确证据 |
| [一般 200 题](app/web/examples/中文一般用例200题.xlsx) | 干扰信息、否定、时序或前置条件 |
| [复杂 200 题](app/web/examples/中文复杂用例200题.xlsx) | 例外、冲突、多重约束和步骤依赖 |

自建题集时保留模板表头，每行写一个问题，同一用例 ID 的多行会组合为一题。上下文可以填写文本或 JSON。候选项每行写一项，格式为 `候选名称：说明`，也可填写 JSON 对象。目前统一评估文本单选，每个问题 2–10 个选项，每个用例最多 16 个问题。公式单元格请先粘贴为值。

启用条件填写为 `前置问题ID=选项名称`，例如 `kind=事实报告`。只有前置问题实际选中指定答案，才运行后续问题。依赖问题填写标准答案时，前置问题的标准答案也须与启用条件一致。

整题准确率要求所有标注答案均正确。请求异常计入错题。没有填写标准答案的用例仅展示模型输出。耗时统计为用例的完整执行时间，含网络与条件分支，批次结果可导出供进一步分析。

## 本地数据与调用范围

题集和批次记录默认写在 `.lab-data/`。如果自定义 `LAB_DATA_DIR`，请选择静态网页目录 `app/web/` 以外的位置。导出文件可能含有题目文本或模型的原始响应，分享前请先检查。

云端模型会收到待判断的上下文、问题和候选项，标准答案留在本地评分。Laya 和 Chinese Decision 在本机工作进程完成推理。场景演示和对比测试返回选择结果，便于在接入实际业务流程前验证模型表现。

## 运行开发测试

```sh
npm test
```

测试使用本地样例和模拟适配器，覆盖题集导入、条件分支、评分、批次隔离与模型接口，无需配置云端密钥或下载模型。HTTP 测试需要允许绑定回环端口。

| 路径 | 内容 |
| --- | --- |
| `app/serve.py` | 本地 HTTP 服务入口 |
| `app/lab/` | Excel 导入、评分、批测、模型适配及本地进程 |
| `app/web/` | 网页界面与示例题集 |
| `tests/lab/` | Python 与前端测试 |
| `tests/fixtures/` | 用于校对复杂题答案与数字的合成题夹具 |

## 许可和来源

项目沿用 [Apache-2.0](LICENSE)。随仓库分发的 Chinese Decision 候选项处理代码遵循其 [MIT 许可](reference/chinese-decision/LICENSE)。应用场景来源和第三方归属见 [NOTICE](NOTICE.md)。
