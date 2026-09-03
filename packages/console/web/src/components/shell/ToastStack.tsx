import { useToasts, dismissToast } from "../../lib/toast";

export function ToastStack() {
  const toasts = useToasts();
  if (toasts.length === 0) return null;
  return (
    <div className="toast-stack" role="status" aria-live="polite">
      {toasts.map((t) => (
        <div key={t.id} className={`toast toast-${t.tone}`}>
          <div className="toast-body">
            <strong>{t.title}</strong>
            {t.message !== "" && <p>{t.message}</p>}
          </div>
          <button type="button" className="toast-close" aria-label="dismiss" onClick={() => dismissToast(t.id)}>
            ×
          </button>
        </div>
      ))}
    </div>
  );
}
