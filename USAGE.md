# OpenSim Vicon 缩放分享版

本工具可独立运行，不要求安装 Codex。使用 Codex 时，可将本文件夹安装为 `opensim-vicon-scale` skill。

## 准备环境和输入

使用能够 `import opensim` 和 `import numpy` 的 Python 环境；必须具备 C3DFileAdapter。已实测 OpenSim Python API 4.5.2，其他版本先通过预检查再运行。OpenSim Python 绑定需按其发行方式安装，不能仅靠本目录自动安装。请参阅 [OpenSim Python 脚本说明](https://opensimconfluence.atlassian.net/wiki/spaces/OpenSim/pages/53085346/Scripting+in+Python)。本包不修改环境，不自动下载软件。

本次验证环境为 Python 3.11.14、NumPy 1.25.2、OpenSim 4.5.2。该记录不表示其他环境不可用，也不保证未经测试的组合兼容。

自行提供兼容的未缩放 CGM24 模型、静态 C3D、配套 VSK、以及模型显示所需的 Geometry 文件。包内不含受试者数据、人体模型或 OpenSim 软件。适用模型和标记约束见 SKILL.md；检查通过不等于模型适合其他标记方案。

## 配置和运行

复制 `assets/config.example.json` 为个人配置，填写文件路径。所有相对路径均以 **JSON 配置文件所在文件夹** 为基准，输出目录必须与输入分开。使用时把命令中的 `python` 换为已配置 OpenSim 的 Python 可执行文件。以下命令在本包根目录执行：

```text
python scripts/run_config.py --config my_config.json --check-only --report reports/preflight
```

会生成 `reports/preflight.md` 和 `.json`，不会执行缩放。阻止运行项会一起列出，返回码为 2。检查包括环境、文件、模型加载、标记名称/局部定义、C3D 数据有效性、时间窗、旋转矩阵、VSK 参数、测力转换条件和输出写权限。写权限检查只产生立即移除的临时文件。

修正配置后，启动完整流程：

```text
python scripts/run_config.py --config my_config.json --report reports/preflight
```

该命令会重新检查，只有没有 ERROR 才启动缩放。`READY_WITH_WARNINGS` 表示可运行但仍有需要人工查看的事项，例如近似身高参考或可视化方向。检查保证的是当前程序所需的输入条件，并不保证 RMS 达标或解剖正确。

`overwrite` 默认 false，保护已有结果；确认需要重跑时设置 true。`max_iterations` 最大为 5，包括初始轮次。身高单位不明时先核实；只有明确确认后才填写 `height_mm`，同时设置 `height_override_confirmed=true`，原始 VSK 不变。

每次迭代和最终结果的目录结构、六个可调点、移动限制、评分及生物力学局限见 SKILL.md 和输出报告。自动校准不能替代人工解剖点位检查或动态 IK 验证。
