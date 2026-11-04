import { useEffect, useRef, type RefObject } from "react";

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** The elements inside `root` that Tab can reach, in order, without the hidden ones. */
export function focusableIn(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((el) => el.getClientRects().length > 0);
}

/** Where Tab or Shift+Tab goes from `current` inside a modal whose reachable elements are `items`: the other end of
 * the list when the keyboard would leave it, else null (the browser's own move is right). */
export function trapTarget<T>(items: T[], current: T | null, shift: boolean): T | null {
  if (items.length === 0) return null;
  const first = items[0];
  const last = items[items.length - 1];
  if (current === null || !items.includes(current)) return shift ? last : first;
  if (!shift && current === last) return first;
  if (shift && current === first) return last;
  return null;
}

/**
 * What a modal (drawer or dialog) owes the keyboard while it is open: focus goes into it, Tab and Shift+Tab stay
 * inside it, Escape closes it, and when it closes the focus returns to what had it before (the button that
 * opened it). Without this a keyboard user tabs on into the page behind the backdrop.
 */
export function useModal(open: boolean, ref: RefObject<HTMLElement | null>, onEscape: () => void): void {
  // The newest callback without re-running the effect: callers pass a new function on every render, and running it
  // again would move the focus back to the opener in the middle of typing.
  const escape = useRef(onEscape);
  escape.current = onEscape;

  // The opener is read while the modal is being rendered for the first time: by the time an effect runs, an
  // autoFocus inside the modal has already taken the focus, and the user would be sent back to that.
  const opener = useRef<HTMLElement | null>(null);
  if (open && opener.current === null && document.activeElement instanceof HTMLElement && document.activeElement !== document.body) {
    opener.current = document.activeElement;
  }

  useEffect(() => {
    if (!open) return;
    const before = opener.current;

    const root = ref.current;
    if (root && !root.contains(document.activeElement)) {
      (focusableIn(root)[0] ?? root).focus({ preventScroll: true });
    }

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        escape.current();
        return;
      }
      if (e.key !== "Tab" || !ref.current) return;
      const current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      const target = trapTarget(focusableIn(ref.current), current, e.shiftKey);
      if (target) {
        e.preventDefault();
        target.focus();
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("keydown", onKey, true);
      opener.current = null;
      if (before && before.isConnected) before.focus({ preventScroll: true });
    };
  }, [open, ref]);
}
