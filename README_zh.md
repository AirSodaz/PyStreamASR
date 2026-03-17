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

1.  **克隆仓库**:
    ```bash
    git clone https://github.com/AirSodaz/PyStreamASR.git
    cd PyStreamASR
    ```

2.  **安装依赖**:

    ```bash
    pip install -r requirements.txt
    ```

3.  **下载模型**:
    请确保将 Sherpa-onnx 模型放置在 `models/` 目录中。
    *   示例：`models/sherpa-onnx-streaming-paraformer-bilingual-zh-en/`

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

### 运行服务

**开发模式:**

```bash
uvicorn main:app --reload
```

**终端服务管理器:**

```powershell
.\scripts\manage_service.bat
```

在 Windows 上，此命令将打开一个带编号的终端菜单，使用 Uvicorn 管理服务，并更新 `.env` 中的 `APP_HOST`、`APP_PORT` 和 `APP_WORKERS`。

在 macOS/Linux 上，使用:

```bash
./scripts/manage_service.sh
```

使用相同的终端 UI，但会结合 `gunicorn.conf.py` 使用 Gunicorn 启动服务，同时仍然应用 `.env` 中的 `APP_HOST`、`APP_PORT` 和 `APP_WORKERS`。

**Linux systemd 部署:**

如果需要在 Linux 上通过 `systemd` 进行常驻部署，请运行：

```bash
sudo ./install.sh
```

该安装流程适用于具备 `systemd`、`python3.12`、已完成配置的 `.env`，以及已放置到 `models/` 目录或 `MODEL_PATH` 所指定位置的模型文件的 Linux 主机。安装脚本会校验 `.env`、创建或复用项目根目录下的 `venv`、安装依赖、生成 `/etc/systemd/system/pystreamasr.service`，并自动启用和启动服务。

安装完成后的常用命令：

```bash
sudo systemctl status pystreamasr --no-pager
sudo systemctl restart pystreamasr
sudo systemctl stop pystreamasr
sudo systemctl start pystreamasr
sudo journalctl -u pystreamasr -n 100 --no-pager
```

**Windows 计划任务部署:**

如果需要在 Windows 上通过任务计划程序进行常驻后台部署，请运行：

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\install.ps1
```

该安装流程适用于已安装 `py.exe` 且可通过 `py -3.12` 访问 Python 3.12、已完成配置的 `.env`，以及已放置到 `models/` 目录或 `MODEL_PATH` 所指定位置的模型文件的 Windows 主机。安装脚本会校验 `.env`、创建或复用项目根目录下的 `venv`、安装依赖、注册一个在当前用户登录时自动启动 Uvicorn 的 `PyStreamASR` 计划任务、立即启动一次该任务，并验证 `http://127.0.0.1:APP_PORT/health`。

支持的安装参数：

```powershell
.\install.ps1 -TaskName PyStreamASR -EnvFile .env -Force
```

`.env` 必须已经包含 `MYSQL_DATABASE_URL`、`MODEL_PATH`、`APP_HOST`、`APP_PORT` 和 `APP_WORKERS`。安装器会将任务的标准输出和错误输出分别写入 `logs/scheduled_task.stdout.log` 与 `logs/scheduled_task.stderr.log`。

安装完成后的常用命令：

```powershell
Get-ScheduledTask -TaskName "PyStreamASR" | Get-ScheduledTaskInfo
Start-ScheduledTask -TaskName "PyStreamASR"
Stop-ScheduledTask -TaskName "PyStreamASR"
Unregister-ScheduledTask -TaskName "PyStreamASR" -Confirm:$false
```

**生产模式 (Uvicorn):**

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
```

**生产模式 (Gunicorn - 仅限 Linux/macOS):**

```bash
gunicorn main:app -c gunicorn.conf.py
```

> **注意:** Gunicorn 不支持 Windows。在 Windows 上，请直接使用 Uvicorn 或通过 Docker/WSL 进行部署。

服务器将在 `http://localhost:8000` 启动。

以 `LOG_LEVEL=DEBUG` 运行时，每个 WebSocket 连接都会在 `logs/debug_audio/` 中写入一个单声道 16 kHz PCM WAV 文件，以便开发人员可以检查输入 ASR 的已解码/重采样音频。

### 检查服务状态

为了快速验证服务是否运行以及模型是否加载，你可以访问 `/health` 接口：

```bash
curl http://localhost:8000/health
```

**预期输出:**

```json
{"status":"ok","config":"loaded","project_name":"PyStreamASR","model_status":"loaded"}
```

### 运行模拟脚本

提供了一个测试脚本来模拟客户端向服务器流式传输音频，并验证实时转录。

```bash
python scripts/simulate_stream.py --file path/to/audio.wav --host ws://127.0.0.1:8000/ws/transcribe/test-session-1
```

**参数:**
*   `--file`: 输入的音频文件路径。支持格式：
    *   原始 G.711: `.alaw`, `.pcma`, `.g711`, `.ulaw`, `.pcmu`, `.mulaw` (必须匹配 `--format`)。
    *   原始 PCM16LE: `.pcm`, `.raw` (需要设置 `--format pcm16le`)。
    *   WAV:
        *   G.711 A-law/μ-law WAV 直接通过（格式必须匹配 `--format`）。
        *   PCM WAV/其他音频会被 `librosa` 加载并转换为流格式。
*   `--host`: WebSocket URL (默认: `ws://localhost:8000/ws/transcribe/test-session-1`)。
*   `--chunk_duration`: 每个分块的持续时间（秒） (默认: `0.6`)。用于控制分块大小和休眠间隔以模拟实时流。
*   `--format`: 流编码格式: `alaw`, `ulaw` 或 `pcm16le` (默认: `alaw`)。
*   `--sample_rate`: 流采样率: `8000` 或 `16000` (默认: `8000`)。G.711 通常为 `8000`。

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
