[English](README.md) | **中文**

# PyStreamASR

PyStreamASR 是一个基于 Python 3.12 和 FastAPI 构建的实时流式自动语音识别 (ASR) 服务。它利用 **Sherpa-onnx**（流式 Paraformer）实现高性能、低延迟的语音转文本功能，适用于实时应用场景。

## 功能特点

*   **实时流媒体**: 基于 WebSocket 的高效音频流传输（支持伪流媒体）。
*   **高性能 ASR**: 由 **Sherpa-onnx**（流式 Paraformer）驱动，支持强大的双语（中英）转录。
*   **音频处理**: 原生支持 G.711 A-law/μ-law 和 PCM16LE (8k/16k)，并具备优化的解码及重采样至 16000Hz PCM 的能力。
*   **非阻塞架构**: 基于 FastAPI 并对网络和数据库操作使用异步 I/O，CPU 密集型的推理任务使用 `loop.run_in_executor`。
*   **数据持久化**:
    *   **内存哈希表**: 用于管理临时会话状态和实时部分结果（热数据）。
    *   **MySQL**: 用于最终转录分段的持久化存储（冷数据）。

## 环境要求

*   **Python 3.12**（强制要求）
*   **MySQL 服务器**
*   **Sherpa-onnx** (`pip install sherpa-onnx`)
*   **g711** (`pip install g711`) - 推荐安装，用于高效的 G.711 解码。

## 安装步骤

1. **克隆仓库**
   ```bash
   git clone https://github.com/AirSodaz/PyStreamASR.git
   cd PyStreamASR
   ```

2. **创建虚拟环境并安装依赖**
   ```powershell
   py -3.12 -m venv venv
   .\venv\Scripts\activate
   pip install -r requirements.txt
   ```

3. **创建 `.env` 并填写必要配置**
   将 `.env.example` 复制为 `.env`，至少配置：
   - `MYSQL_DATABASE_URL`
   - `MODEL_PATH`

4. **下载 Sherpa-onnx 模型**
   将模型目录放到 `models/` 下，例如：
   - `models/sherpa-onnx-streaming-paraformer-bilingual-zh-en/`

## 配置指南

在项目根目录创建一个 `.env` 文件。如果存在 `.env.example`，你可以直接复制它。

```ini
PROJECT_NAME="PyStreamASR"
MYSQL_DATABASE_URL="mysql+aiomysql://user:password@localhost/dbname"
# 相对于项目根目录的路径
MODEL_PATH="models/sherpa-onnx-streaming-paraformer-bilingual-zh-en"
LOG_LEVEL="INFO"
LOG_DIR="logs"
RETURN_TRANSCRIPTION=true
AUDIO_INPUT_FORMAT="alaw"  # alaw | ulaw | pcm16le
AUDIO_SOURCE_RATE=8000     # 8000 | 16000
APP_HOST="0.0.0.0"
APP_PORT=8000
APP_WORKERS=1
```

### .env 变量说明 (必填/选填 + 选项)

| 变量 | 必填 | 默认值 | 选项 / 备注 |
| --- | --- | --- | --- |
| `MYSQL_DATABASE_URL` | 是 | 无 | SQLAlchemy DSN。示例：`mysql+aiomysql://user:password@host/dbname`。 |
| `MODEL_PATH` | 是 | 无 | 模型目录路径。可以使用绝对路径或相对于项目的相对路径。 |
| `PROJECT_NAME` | 否 | `PyStreamASR` | 任意字符串。用于应用标题和 `/health` 接口。 |
| `LOG_LEVEL` | 否 | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`。设置为 `DEBUG` 时，处理后的连接音频将保存为 WAV 文件到 `logs/debug_audio/` 目录下。 |
| `LOG_DIR` | 否 | `logs` | 日志文件存放目录。 |
| `RETURN_TRANSCRIPTION` | 否 | `true` | `true` 或 `false`。设置为 `false` 时，服务器依然会进行处理，但不会通过 WebSocket 返回转录消息。 |
| `AUDIO_INPUT_FORMAT` | 否 | `alaw` | `alaw`, `ulaw`, `pcm16le`。必须与客户端发送的流格式一致。 |
| `AUDIO_SOURCE_RATE` | 否 | `8000` | `8000` 或 `16000`。必须与客户端发送的流采样率一致。 |
| `APP_HOST` | 否 | `0.0.0.0` | 终端服务管理器使用的绑定主机。 |
| `APP_PORT` | 否 | `8000` | 终端服务管理器使用的绑定端口。 |
| `APP_WORKERS` | 否 | `1` | 终端服务管理器使用的 worker 数量。在 Windows 上应用于 Uvicorn；在 macOS/Linux 上应用于 Gunicorn。 |

## 使用方法

### 快速开始

开发模式启动服务：

```bash
uvicorn main:app --reload
```

检查服务是否就绪：

```bash
curl http://localhost:8000/health
```

预期响应：

```json
{"status":"ok","config":"loaded","project_name":"PyStreamASR","model_status":"loaded"}
```

运行流式模拟脚本：

```bash
python scripts/simulate_stream.py --file path/to/audio.wav --host ws://127.0.0.1:8000/ws/transcribe/test-session-1
```

常用参数：
- `--format`：`alaw`、`ulaw` 或 `pcm16le`
- `--sample_rate`：`8000` 或 `16000`
- `--chunk_duration`：每个分片的时长和发送间隔（秒）

### 服务管理与部署

| 场景 | 命令 | 说明 |
| --- | --- | --- |
| 本地服务管理（Windows） | `.\scripts\manage_service.bat` | 打开终端 TUI，并更新 `.env` 中的 `APP_HOST`、`APP_PORT`、`APP_WORKERS`。 |
| 本地服务管理（macOS/Linux） | `./scripts/manage_service.sh` | 在类 Unix 环境下使用同一套终端 TUI。 |
| Linux 常驻服务 | `sudo ./install.sh` | 创建或复用 `venv`、安装依赖、注册 `pystreamasr.service`，并安装 `pystreamasr` 辅助命令。 |
| Windows 常驻服务 | `powershell.exe -ExecutionPolicy Bypass -File .\install.ps1` | 创建或复用 `venv`、注册 `PyStreamASR` 计划任务，并安装 `pystreamasr` 辅助命令。 |
| 直接生产运行 | `uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4` | 跨平台的生产启动方式。 |
| Gunicorn（仅 Linux/macOS） | `gunicorn main:app -c gunicorn.conf.py` | Windows 不支持 Gunicorn。 |

运行安装脚本后，可以使用：

```bash
pystreamasr
```

如果 Linux 主机对 `systemctl` 需要提权控制，请使用 `sudo pystreamasr`。

当 `LOG_LEVEL=DEBUG` 时，每个 WebSocket 连接都会在 `logs/debug_audio/` 下额外写出 16 kHz 单声道 PCM WAV 文件，便于排查音频解码和重采样问题。

## 项目结构

```text
PyStreamASR/
├── api/             # API 接口 (WebSocket 逻辑)
├── core/            # 配置及全局设置
├── models/          # 预训练的 Sherpa-onnx 模型
├── services/        # 业务逻辑 (音频, 推理, 存储, Schema)
├── scripts/         # 实用工具和测试脚本 (如 simulate_stream.py)
├── main.py          # 应用入口
└── requirements.txt # 项目依赖
```
