"""结构化 JSONL Trace；写入失败不影响业务结果。"""

import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TRACE_PATH = PROJECT_ROOT / "data" / "traces.jsonl"
_WRITE_LOCK = threading.Lock()


def new_run_id() -> str:
    """生成一次对话或一次确认动作的关联编号。"""
    return f"run_{uuid4().hex}"


def _enabled() -> bool:
    value = os.environ.get("TRACE_ENABLED", "true").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _trace_path() -> Path:
    configured = os.environ.get("TRACE_PATH")
    return Path(configured) if configured else DEFAULT_TRACE_PATH


def emit_trace(event: str, **fields) -> None:
    """追加一条 JSONL 事件；任何写入异常都只忽略，不向调用方抛出。"""
    if not _enabled():
        return

    payload = {
        "ts": datetime.now(UTC).isoformat(),
        "event": event,
        **fields,
    }
    try:
        path = _trace_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
        with _WRITE_LOCK, path.open("a", encoding="utf-8") as file:
            file.write(line + "\n")
    except Exception:
        # Trace 是辅助设施，不能影响聊天和业务写入。
        return