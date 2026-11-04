import { AnimatePresence, motion } from "motion/react";
import { TriangleAlert } from "lucide-react";
import { useId, useRef, type ReactNode } from "react";
import { useModal } from "../../hooks/useModal";

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
  const ref = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const textId = useId();
  useModal(open, ref, onCancel);

  return (
    <AnimatePresence>
      {open && (
        <motion.div className="modal-backdrop" onClick={onCancel} initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.15 }}>
          <motion.div
            ref={ref}
            className="dialog card"
            role="alertdialog"
            aria-modal="true"
            aria-labelledby={titleId}
            aria-describedby={textId}
            tabIndex={-1}
            onClick={(e) => e.stopPropagation()}
            initial={{ opacity: 0, scale: 0.96, y: 8 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.97 }}
            transition={{ duration: 0.16 }}
          >
            <span className="dialog-icon">
              <TriangleAlert size={22} />
            </span>
            <h2 id={titleId}>{title}</h2>
            <div className="muted" id={textId}>{children}</div>
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
