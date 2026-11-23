/*
 * @author: Ngoc Luong <ngocluongg0101@gmail.com>
 */

import type { ReactNode } from "react";

export type BadgeTone = "ok" | "warn" | "bad" | "idle" | "accent";

export function Badge({ tone, children }: { tone: BadgeTone; children: ReactNode }) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}
