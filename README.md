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
