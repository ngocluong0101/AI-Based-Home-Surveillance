import { AnimatePresence, motion } from "motion/react";
import { TriangleAlert } from "lucide-react";
import { useEffect, type ReactNode } from "react";

/** A small modal asking "are you sure?"; the confirm button is red for something that cannot be undone. */
export function ConfirmDialog({
  open,
  title,
  children,
  confirmLabel,
  busy,
  error,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: string;
  children: ReactNode;
  confirmLabel: string;
  busy?: boolean;
  error?: string;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (k: KeyboardEvent) => k.key === "Escape" && onCancel();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onCancel]);

  return (
    <AnimatePresence>
      {open && (
        <motion.div className="modal-backdrop" onClick={onCancel} initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.15 }}>
          <motion.div
            className="dialog card"
            role="alertdialog"
            aria-modal="true"
            aria-label={title}
            onClick={(e) => e.stopPropagation()}
            initial={{ opacity: 0, scale: 0.96, y: 8 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.97 }}
            transition={{ duration: 0.16 }}
          >
            <span className="dialog-icon">
              <TriangleAlert size={22} />
            </span>
            <h2>{title}</h2>
            <div className="muted">{children}</div>
            {error && <p className="err-text">{error}</p>}
            <div className="dialog-actions">
              <button className="btn" onClick={onCancel} autoFocus>
                Cancel
              </button>
              <button className="btn btn-danger" disabled={busy} onClick={onConfirm}>
                {confirmLabel}
              </button>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
