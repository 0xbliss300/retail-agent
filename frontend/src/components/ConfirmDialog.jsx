import { useEffect } from "react";
import Icon from "./Icons.jsx";
import { formatAmount } from "../format.js";

export default function ConfirmDialog({
  title,
  description,
  summary,
  confirmLabel = "确认",
  cancelLabel = "取消",
  busy = false,
  error = "",
  onConfirm,
  onCancel,
}) {
  useEffect(() => {
    const onKeyDown = (event) => {
      if (event.key === "Escape" && !busy) onCancel();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [busy, onCancel]);

  return (
    <div className="dialog-backdrop" role="presentation" onClick={() => !busy && onCancel()}>
      <div
        className="dialog"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="dialog__head">
          <h2 className="dialog__title">{title}</h2>
          <button className="dialog__close" type="button" onClick={onCancel} disabled={busy} aria-label="关闭">
            <Icon name="close" size={16} />
          </button>
        </div>
        <p className="dialog__body">{description}</p>
        {summary ? (
          <div className="dialog__summary">
            <Icon name="box" size={20} />
            <span>{summary.id}</span>
            <span>{formatAmount(summary.amountCents)}</span>
            <span className="pill pill--neutral">{summary.badge}</span>
          </div>
        ) : null}
        {error ? <div className="dialog__error">{error}</div> : null}
        <div className="dialog__actions">
          <button className="btn btn--outline" type="button" onClick={onCancel} disabled={busy}>
            {cancelLabel}
          </button>
          <button className="btn btn--primary" type="button" onClick={onConfirm} disabled={busy}>
            {busy ? "处理中…" : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
