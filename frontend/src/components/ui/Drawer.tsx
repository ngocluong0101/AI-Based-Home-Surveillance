import { AnimatePresence, motion } from "motion/react";
import { X } from "lucide-react";
import { useId, useRef, type ReactNode } from "react";
import { useModal } from "../../hooks/useModal";

/** Slide-in panel. Closes on Esc or a click outside; the children are the body and the footer. While it is open the
 * keyboard stays inside it and gets back to where it was when it closes (see useModal). */
export function Drawer({
  open,
  title,
  onClose,
  children,
}: {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLElement>(null);
  const titleId = useId();
  useModal(open, ref, onClose);

  return (
    <AnimatePresence>
      {open && (
        <>
          <motion.div
            key="backdrop"
            className="backdrop"
            onClick={onClose}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.18 }}
          />
          <motion.aside
            key="drawer"
            ref={ref}
            className="drawer"
            role="dialog"
            aria-modal="true"
            aria-labelledby={titleId}
            tabIndex={-1}
            initial={{ x: "100%" }}
            animate={{ x: 0 }}
            exit={{ x: "100%" }}
            transition={{ type: "tween", duration: 0.22, ease: [0.2, 0.8, 0.2, 1] }}
          >
            <div className="drawer-head">
              <h2 id={titleId}>{title}</h2>
              <button className="btn btn-ghost btn-icon" onClick={onClose} aria-label="Close">
                <X size={18} />
              </button>
            </div>
            {children}
          </motion.aside>
        </>
      )}
    </AnimatePresence>
  );
}
