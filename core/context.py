from contextvars import ContextVar

# Context variable to store the session ID for the current request/connection
# Default value is "-" to indicate no session context
session_id_ctx: ContextVar[str] = ContextVar("session_id", default="-")

# Context variable to store the per-WebSocket connection ID for log correlation.
# A single session can reconnect multiple times, so this distinguishes attempts.
connection_id_ctx: ContextVar[str] = ContextVar("connection_id", default="-")
