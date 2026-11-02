# OpenSim Vicon Scale

从 Vicon 静态 C3D 与 VSK 生成兼容 gait2392 CGM24 的缩放模型，支持运行前检查、分轴缩放、最多五轮有限标记校准，以及逐轮结果保存和中文质量报告。

## 使用

1. 准备可导入 OpenSim 和 NumPy 的 Python 环境，以及自己的兼容模型、静态 C3D、VSK 和显示几何文件。
2. 复制 `assets/config.example.json` 为 `my_config.json`，填写路径。相对路径以配置文件所在目录为基准。
3. 先检查，再运行：

```sh
python scripts/run_config.py --config my_config.json --check-only --report reports/preflight
python scripts/run_config.py --config my_config.json --report reports/preflight
```

发现阻止项时不会启动缩放。查看 [完整使用说明](USAGE.md) 和 [skill 工作流](SKILL.md)。不使用 Codex 也可直接运行脚本；Codex 用户可将仓库文件夹作为 `opensim-vicon-scale` skill 使用。

## 适用范围

仅适用于所述 CGM24 模型、标记定义和经确认的坐标转换。已测试 Python 3.11.14、OpenSim Python API 4.5.2 和 NumPy 1.25.2。其他环境须预检和实际验证。

五轮校准最多只调整六个跖骨头模型标记，固定骨架缩放测量点；误差下降并非独立解剖验证。左右胫骨宽度共用右踝测量等假设在 skill 和报告中明确记录。RMS 与最大误差仅为检查依据，还需人工姿态、点位和动态 IK 验证。

仓库不包含受试者数据、人体模型、OpenSim 软件、个人配置或实验输出；使用者自行提供这些输入。

## v1.2.0：点名称自动对齐

预检查会自动检查模型与 Vicon 点名。运行时生成 `aligned_model.osim`，将可靠匹配的模型点改为 C3D 原始名称，并同步缩放测量和 IK 配置；原始模型及点位置不因改名而改变。支持明确别名、大小写/标点及常见受试者前缀；歧义会停止，已确认的自定义映射可写入配置 `marker_name_map`。

`点名称对齐报告.md` / `marker_alignment.json` 列出对应关系。实验数据中没有模型对应的候选点，如需分析，应手动添加模型点及其身体归属、局部位置。疑似派生量和未标记点单独列出供核实，不自动生成解剖点。
