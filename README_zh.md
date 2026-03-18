[English](README.md) | **中文**

# PyStreamASR

PyStreamASR 是一个面向流式语音转文本场景的 FastAPI 实时 ASR 服务。它通过 WebSocket 接收音频流，将输入的 G.711 或 PCM 音频转换为 16 kHz PCM 供 Sherpa-onnx 流式识别使用，向客户端返回 partial 和 final 转录事件，使用内存保存会话级中间结果，并将最终分段持久化到 MySQL。

## 核心概览

- 健康检查接口：`GET /health`
- 流式转录接口：`WebSocket /ws/transcribe/{session_id}`
- 输入音频格式：`alaw`、`ulaw`、`pcm16le`
- 推理内部音频：单声道 16 kHz float32 PCM
- 中间结果：按会话保存在内存中
- 最终结果：写入 MySQL `segments` 表
- 推理模型：Sherpa-onnx Paraformer Streaming

## 快速开始

1. 克隆仓库。

   ```bash
   git clone https://github.com/AirSodaz/PyStreamASR.git
   cd PyStreamASR
   ```

2. 创建并激活 Python 3.12 虚拟环境。

   ```powershell
   py -3.12 -m venv venv
   .\venv\Scripts\activate
   ```

3. 安装依赖。

   ```powershell
   pip install -r requirements.txt
   ```

4. 创建本地环境变量文件。

   ```powershell
   Copy-Item .env.example .env
   ```

5. 至少配置以下变量：
   - `MYSQL_DATABASE_URL`
   - `MODEL_PATH`

6. 将 Sherpa-onnx Paraformer Streaming 模型放到：

   ```text
   models/sherpa-onnx-streaming-paraformer-bilingual-zh-en/
   ```

   当前模型加载器会在该目录下查找 `encoder.int8.onnx`、`decoder.int8.onnx`、`tokens.txt` 等文件。

7. 启动开发服务器。

   ```bash
   uvicorn main:app --reload
   ```

### Uvicorn 开发模式参数说明

本地开发时，最常用的 `uvicorn` 参数如下：

| 参数 | 示例 | 作用 |
| --- | --- | --- |
| `--reload` | `uvicorn main:app --reload` | 监听 Python 源码变化并自动重启服务。仅建议在开发环境使用。 |
| `--host` | `--host 0.0.0.0` | 控制监听地址。只本机访问时可用 `127.0.0.1`，需要让其他设备接入时用 `0.0.0.0`。 |
| `--port` | `--port 8000` | 控制 `/health` 和 `/ws/transcribe/{session_id}` 暴露的端口。 |
| `--workers` | `--workers 4` | 启动多个 worker 进程。开发环境通常不需要，也不应与 `--reload` 同时使用。 |

推荐的开发命令：

```bash
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

如果你希望直接使用 `.env` 中的配置，也可以执行 `python main.py`。它会读取 `APP_HOST` 和 `APP_PORT`，并以 reload 模式启动 Uvicorn。

## 快速验证

### 1. 确认服务已启动

打开：

```text
http://localhost:8000/health
```

期望返回结构：

```json
{
  "status": "ok",
  "config": "loaded",
  "project_name": "PyStreamASR",
  "model_status": "loaded"
}
```

### 2. 验证 WebSocket 转录链路

激活虚拟环境后执行：

```powershell
python scripts/simulate_stream.py --file .\path\to\your_audio.wav --host ws://localhost:8000/ws/transcribe/demo-session
```

请将 `.\path\to\your_audio.wav` 替换为实际存在的音频文件。正常情况下，客户端输出会持续看到 `partial` 和 `final` 两类 JSON 消息。具体转录文本取决于输入音频和模型表现。

## 流式处理流程

PyStreamASR 对每个连接的处理链路如下：

1. 客户端向 `WebSocket /ws/transcribe/{session_id}` 发送二进制音频分片。
2. `AudioProcessor` 负责解码 `alaw`、`ulaw` 或 `pcm16le`，归一化采样值，并在需要时重采样到 16 kHz。
3. 音频处理和模型推理通过 `loop.run_in_executor` 执行，避免阻塞事件循环中的 WebSocket 和数据库 I/O。
4. 中间转录结果以会话为粒度保存在内存中。
5. 最终分段写入 MySQL，并通过 WebSocket 作为 `final` 事件返回。
6. 如果使用相同的 `session_id` 重连，会沿用该会话的序号继续处理。

## 支持的输入格式

| 格式 | 推荐源采样率 | 说明 |
| --- | --- | --- |
| `alaw` | 通常为 `8000` Hz | G.711 A-law，适合话务类音频流。 |
| `ulaw` | 通常为 `8000` Hz | G.711 mu-law，适合话务类音频流。 |
| `pcm16le` | `8000` 或 `16000` Hz | 原始 little-endian 16-bit PCM。`8000` Hz 输入会在服务端重采样。 |

所有输入都会在推理前被规范化并转换为单声道 16 kHz PCM。对于 G.711 流，请确保客户端实际发送的格式和采样率与 `AUDIO_INPUT_FORMAT`、`AUDIO_SOURCE_RATE` 保持一致。

## 配置重点

在项目根目录创建 `.env` 文件。一个典型配置如下：

```ini
PROJECT_NAME=PyStreamASR
MYSQL_DATABASE_URL=mysql+aiomysql://root:password@localhost/pystreamasr
MODEL_PATH=models/sherpa-onnx-streaming-paraformer-bilingual-zh-en
LOG_LEVEL=INFO
LOG_DIR=logs
RETURN_TRANSCRIPTION=true
AUDIO_INPUT_FORMAT=alaw
AUDIO_SOURCE_RATE=8000
APP_HOST=0.0.0.0
APP_PORT=8000
APP_WORKERS=1
```

| 变量 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `MYSQL_DATABASE_URL` | 是 | 无 | Async SQLAlchemy DSN，例如 `mysql+aiomysql://user:password@host/dbname`。 |
| `MODEL_PATH` | 是 | 无 | 建议与实际模型目录保持一致。当前加载器默认从 `models/sherpa-onnx-streaming-paraformer-bilingual-zh-en/` 读取模型文件。 |
| `PROJECT_NAME` | 否 | `PyStreamASR` | 用于 FastAPI 应用标题和 `/health` 返回内容。 |
| `LOG_LEVEL` | 否 | `INFO` | 设为 `DEBUG` 时，会输出用于排障的 WAV 调试音频。 |
| `LOG_DIR` | 否 | `logs` | 运行日志和调试产物的基础目录。 |
| `RETURN_TRANSCRIPTION` | 否 | `true` | 设为 `false` 时，服务仍会处理和存储结果，但不会通过 WebSocket 回传转录消息。 |
| `AUDIO_INPUT_FORMAT` | 否 | `alaw` | 可选 `alaw`、`ulaw`、`pcm16le`，必须与客户端发送格式一致。 |
| `AUDIO_SOURCE_RATE` | 否 | `8000` | 可选 `8000`、`16000`，必须与客户端发送采样率一致。 |
| `APP_HOST` | 否 | `0.0.0.0` | 本地启动和服务封装脚本使用的监听地址。 |
| `APP_PORT` | 否 | `8000` | 本地启动和服务封装脚本使用的监听端口。 |
| `APP_WORKERS` | 否 | `1` | 服务封装脚本使用的 worker 数。Windows 下配合 Uvicorn 使用，Linux/macOS 下配合 Gunicorn 使用。 |

当 `LOG_LEVEL=DEBUG` 时，每个 WebSocket 会话都会在 `logs/debug_audio/` 下生成一个 16 kHz 单声道 WAV 文件，便于检查解码和重采样后的音频内容。

## 部署方式

按运行环境选择合适的启动方式：

| 场景 | 命令 | 说明 |
| --- | --- | --- |
| 跨平台直接运行 | `uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4` | 简单直接的生产式启动方式。 |
| Windows 后台服务 | `powershell.exe -ExecutionPolicy Bypass -File .\install.ps1` | 注册 `PyStreamASR` 计划任务，并安装 `pystreamasr` 命令。 |
| Linux 常驻服务 | `sudo ./install.sh` | 安装 `pystreamasr.service` systemd 服务，并安装 `pystreamasr` 命令。 |
| Linux/macOS Gunicorn | `gunicorn main:app -c gunicorn.conf.py` | Windows 不支持 Gunicorn。 |

安装脚本完成后，可使用：

```bash
pystreamasr
```

在 Linux 上如果服务控制需要提权，请使用 `sudo pystreamasr`。

### `pystreamasr` 命令说明

`pystreamasr` 是在 `pyproject.toml` 中定义的控制台入口。它不会直接启动 ASR 服务，而是打开一个交互式终端服务管理器。

菜单能力包括：

- `View Status`：查看当前运行时、监听地址、端口、worker 数、后端类型以及 `/health` 检查结果
- `Start` / `Stop` / `Restart`：控制已安装的后台服务
- `Modify Host` / `Modify Port` / `Modify Workers`：直接修改 `.env` 中的 `APP_HOST`、`APP_PORT`、`APP_WORKERS`

具体行为取决于安装方式：

- 在 Windows 上，它管理 `PyStreamASR` 计划任务，对应 Uvicorn 运行时。
- 在 Linux 上，它管理 `pystreamasr.service` systemd 单元，对应 Gunicorn 运行时。

典型使用流程：

1. 先运行 `install.ps1` 或 `install.sh`。
2. 执行 `pystreamasr`。
3. 在菜单中查看状态或调整 host、port、workers。
4. 修改配置后，在同一菜单中重启服务使配置生效。

如果服务尚未安装，`pystreamasr` 仍可打开，但执行服务控制操作时会提示当前受管服务尚未安装。

## 项目结构

当前仓库结构比最小运行时目录更完整，主要包含以下部分：

```text
PyStreamASR/
├── api/               # WebSocket 路由与连接生命周期
├── core/              # 配置、日志、请求上下文
├── docs/              # 中英文 API 文档
├── models/            # Sherpa-onnx 模型文件
├── scripts/           # 流式模拟脚本、安装脚本、服务管理器
├── services/          # 音频、推理、存储、数据库 Schema 逻辑
├── main.py            # FastAPI 入口与 lifespan 初始化
├── install.ps1        # Windows 安装与计划任务注册
├── install.sh         # Linux 安装与 systemd 注册
├── pyproject.toml     # 包元数据与控制台入口
└── requirements.txt   # Python 依赖列表
```

## 文档

- 英文 API 文档：[docs/API.md](docs/API.md)
- 中文 API 文档：[docs/API_zh.md](docs/API_zh.md)

README 侧重安装、启动和联调验证；更完整的消息格式和接口示例请查看 API 文档。

## 常见问题

- 启动时报 `FileNotFoundError`，通常表示 Sherpa-onnx 模型文件没有放到 `models/sherpa-onnx-streaming-paraformer-bilingual-zh-en/` 下。
- 数据库连接失败，通常是 `MYSQL_DATABASE_URL` 配置错误、MySQL 未启动，或目标数据库不存在。
- WebSocket 一直没有收到转录消息时，先检查 `RETURN_TRANSCRIPTION=true`。
- 转录效果异常或音频处理报错时，优先确认客户端实际发送的格式和采样率是否与 `AUDIO_INPUT_FORMAT`、`AUDIO_SOURCE_RATE` 一致。
- 需要排查解码后的音频内容时，可将 `LOG_LEVEL=DEBUG`，然后检查 `logs/debug_audio/` 下生成的 WAV 文件。
