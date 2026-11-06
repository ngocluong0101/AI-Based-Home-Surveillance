import type { ReactNode } from "react";

export interface SegmentedOption<T extends string> {
  value: T;
  label: string;
  icon?: ReactNode;
}

/** A row of exclusive choices (a view switch). Each is a toggle button, so a screen reader hears which one is on. */
export function Segmented<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T;
  options: SegmentedOption<T>[];
  onChange: (value: T) => void;
}) {
  return (
    <div className="segmented" role="group" aria-label={label}>
      {options.map((o) => (
        <button key={o.value} type="button" className={o.value === value ? "on" : ""} aria-pressed={o.value === value} onClick={() => onChange(o.value)}>
          {o.icon}
          {o.label}
        </button>
      ))}
    </div>
  );
}
