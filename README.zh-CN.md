# AgentXR

[English](README.md) | 简体中文

面向动手操作的相机助手。AgentXR 使用 Codex 理解操作现场、跟踪任务进度，并通过画面标注提示下一步。

## 核心功能

- **视觉指导**：拍照分析或持续采样相机画面，流式显示操作指引、目标框和箭头。
- **任务上下文**：记录目标、步骤、反馈与观察结果，并在本地保存会话。
- **面包板支持**：标定孔格、复核端点候选，再用确定性电路规则检查已确认的连接。

## 快速开始

需要 Python 3.10+、已登录的 Codex CLI，以及支持摄像头的 Safari 或 Chrome 等浏览器。基础观察功能仅使用 Python 标准库，无需安装 npm 依赖。

```sh
git clone git@github.com:SoniaSuns/AgentXR.git
cd AgentXR
python3 server.py
```

打开 [localhost:8765](http://localhost:8765)，开启摄像头，输入任务，然后点击 **拍照并分析** 或 **开始流式观察**。macOS 也可双击 `start.command` 启动。

## 可选：面包板工具

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-vision.txt
.venv/bin/python server.py
```

冻结清晰画面、完成板面标定并确认端点后，再检查连接。模型候选需要人工复核，目前不支持无人监督的照片转网表。标定与测量方法见[面包板使用指南](docs/BREADBOARD.md)。

## 文档

- [系统架构](ARCHITECTURE.md)
- [面包板工作流](docs/BREADBOARD.md)
- 配置：在 `settings.json` 中覆盖 `config.py` 的默认值。

## 范围与数据

AgentXR 是使用二维标注的本地研究原型，只分析选中的照片，不逐帧处理视频。照片会发送至 Codex 使用的模型服务；默认在本地保存会话及图片。电路检查依赖板型配置与已确认的输入，仍需实物验证。
