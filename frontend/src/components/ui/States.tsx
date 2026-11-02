import type { LucideIcon } from "lucide-react";
import { AlertTriangle } from "lucide-react";
import type { ReactNode } from "react";

export function EmptyState({
  icon: Icon,
  title,
  hint,
  compact,
  children,
}: {
  icon: LucideIcon;
  title: string;
  hint?: ReactNode;
  compact?: boolean;
  children?: ReactNode;
}) {
  return (
    <div className={`state ${compact ? "state-compact" : ""}`}>
      <span className="state-icon">
        <Icon size={22} />
      </span>
      <strong>{title}</strong>
      {hint && <p>{hint}</p>}
      {children}
    </div>
  );
}

export function ErrorState({
  title,
  message,
  onRetry,
  compact,
}: {
  title: string;
  message?: string;
  onRetry?: () => void;
  compact?: boolean;
}) {
  return (
    <div className={`state state-error ${compact ? "state-compact" : ""}`} role="alert">
      <span className="state-icon">
        <AlertTriangle size={22} />
      </span>
      <strong>{title}</strong>
      {message && <p>{message}</p>}
      {onRetry && (
        <button className="btn btn-sm" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  );
}
