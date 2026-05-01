[English](API.md) | **中文**

# PyStreamASR API 文档

## 实时语音转录

### 方法与 URL

```
WebSocket /ws/transcribe/{session_id}
```

### 描述

建立一个 WebSocket 连接，用于实时语音转文本转录。客户端可以流式传输以 G.711 格式 (8kHz) 编码的音频数据，服务器会以部分（中间）或最终分段的形式返回转录结果。

此接口支持会话连续性——使用相同的 `session_id` 重新连接将从上一个已知的序列号继续。

---

### 路径参数

| 名称         | 类型   | 是否必填 | 描述                                       |
|--------------|--------|----------|--------------------------------------------|
| `session_id` | string | 是       | 转录会话的唯一标识符。                     |

---

### 请求头

WebSocket 连接遵循标准的 HTTP 升级握手协议 ([RFC 6455](https://datatracker.ietf.org/doc/html/rfc6455))。不需要自定义请求头。

---

### 客户端 → 服务器消息

**格式:** 二进制 (原始字节流)

| 字段       | 类型   | 描述                                                           |
|------------|--------|----------------------------------------------------------------|
| 音频数据  | bytes  | 以 8kHz 采样率进行 G.711 编码的音频（μ-law 或 A-law）。 |

---

### 服务器 → 客户端消息

**格式:** JSON

#### 部分结果 (Partial)

在中间转录可用（尚未最终确认）时发送。

```json
{
  "type": "partial",
  "text": "hello wor",
  "seq": 5
}
```

| 字段   | 类型    | 描述                                         |
|--------|---------|----------------------------------------------|
| `type` | string  | 对于中间结果，始终为 `"partial"`。           |
| `text` | string  | 当前的转录假设。                             |
| `seq`  | integer | 用于排序的序列号。                           |

#### 最终结果 (Final)

在一个分段被最终确认并持久化到数据库时发送。

```json
{
  "type": "final",
  "text": "hello world",
  "seq": 5
}
```

| 字段   | 类型    | 描述                                         |
|--------|---------|----------------------------------------------|
| `type` | string  | 对于确认的转录，始终为 `"final"`。           |
| `text` | string  | 最终确认的转录文本。                         |
| `seq`  | integer | 分段序列号（已持久化）。                     |

#### 错误事件 (Error)

当推理容量耗尽并即将关闭连接时发送。

```json
{
  "type": "error",
  "code": "inference_overloaded",
  "message": "ASR inference is overloaded; retry later.",
  "retryable": true
}
```

| 字段        | 类型    | 描述                                         |
|-------------|---------|----------------------------------------------|
| `type`      | string  | 对于服务端错误事件，始终为 `"error"`。       |
| `code`      | string  | 机器可读的错误码。                           |
| `message`   | string  | 人类可读的错误信息。                         |
| `retryable` | boolean | 客户端是否可以稍后重试。                     |

---

### 示例请求

**JavaScript (浏览器)**

```javascript
const sessionId = "user-123-session-456";
const ws = new WebSocket(`ws://localhost:8000/ws/transcribe/${sessionId}`);

ws.onopen = () => {
  console.log("Connected");
  // 以二进制形式流式传输音频数据
  navigator.mediaDevices.getUserMedia({ audio: true })
    .then(stream => {
      // 处理并发送 G.711 编码的音频分块
      // ws.send(audioChunk);
    });
};

ws.onmessage = (event) => {
  const result = JSON.parse(event.data);
  if (result.type === "final") {
    console.log(`Final [${result.seq}]: ${result.text}`);
  } else {
    console.log(`Partial [${result.seq}]: ${result.text}`);
  }
};

ws.onclose = () => console.log("Disconnected");
```

**Python (websockets 库)**

```python
import asyncio
import websockets

async def stream_audio():
    uri = "ws://localhost:8000/ws/transcribe/user-123-session-456"
    async with websockets.connect(uri) as ws:
        # 发送 G.711 音频字节流
        with open("audio.g711", "rb") as f:
            while chunk := f.read(320):  # 20ms 帧
                await ws.send(chunk)

                # 接收转录结果
                response = await ws.recv()
                print(response)

asyncio.run(stream_audio())
```

---

### 示例响应

#### 成功 — 部分转录

```json
{
  "type": "partial",
  "text": "the quick brown",
  "seq": 3
}
```

#### 成功 — 最终转录

```json
{
  "type": "final",
  "text": "the quick brown fox jumps over the lazy dog",
  "seq": 3
}
```

#### 连接关闭 (正常)

当客户端断开连接时，服务器会优雅地关闭 WebSocket。不会发送任何错误负载。

---

### 错误处理

| 场景                      | 行为                                                     |
|---------------------------|----------------------------------------------------------|
| 音频格式无效              | 连接保持打开；服务器端记录错误日志。                     |
| 处理错误                  | 跳过当前帧；后续音频块将正常处理。                       |
| 推理过载                  | 发送 `error` 事件，然后使用关闭码 `1013` 关闭连接。       |
| 数据库错误                | 记录日志；仍可能会返回部分结果。                         |
| 意外的服务器错误          | 连接关闭；记录附带堆栈跟踪的错误日志。                   |

---

### 注意事项

- **音频格式:** 输入必须是 8kHz 采样的 G.711 编码。服务器会在内部将其重采样为 16kHz 供 ASR 模型使用。
- **会话持久化:** 最终的转录内容存储在 MySQL 中；部分结果缓存在内存中。
- **重新连接:** 使用相同的 `session_id` 会从上一个序列号恢复。
- **并发:** 音频处理通过默认 executor 运行，ASR 推理使用有界线程池。推理容量耗尽时，客户端会收到 `code=inference_overloaded`，随后 WebSocket 以关闭码 `1013` 关闭。
