from contextvars import ContextVar

# Context variable to store the session ID for the current request/connection
# Default value is "-" to indicate no session context
session_id_ctx: ContextVar[str] = ContextVar("session_id", default="-")
