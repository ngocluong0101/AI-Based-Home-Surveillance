import type { LucideIcon } from "lucide-react";
import { Skeleton } from "./Skeleton";

export function StatCard({
  icon: Icon,
  label,
  value,
  hint,
  tone = "accent",
  loading,
}: {
  icon: LucideIcon;
  label: string;
  value: string | number | undefined;
  hint?: string;
  tone?: "accent" | "ok" | "warn" | "idle";
  loading?: boolean;
}) {
  return (
    <div className="card stat">
      <span className={`stat-icon ${tone === "accent" ? "" : tone}`}>
        <Icon size={20} />
      </span>
      <div>
        <div className="stat-label">{label}</div>
        {loading ? <Skeleton width={56} height={26} style={{ margin: "4px 0" }} /> : <div className="stat-value">{value ?? "-"}</div>}
        {hint && <div className="stat-hint">{hint}</div>}
      </div>
    </div>
  );
}
