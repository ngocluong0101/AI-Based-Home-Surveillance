export function formatTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export const percent = (x: number): string => `${Math.round(x * 100)}%`;

export function seconds(value: number | null | undefined): string {
  if (value === null || value === undefined) return "-";
  return value < 10 ? `${value.toFixed(1)} s` : `${Math.round(value)} s`;
}

/** "2024-05-01" from a date input to the ISO instant of local midnight; undefined for empty input. */
export function localDayStart(value: string, plusDays = 0): string | undefined {
  if (!value) return undefined;
  const [y, m, d] = value.split("-").map(Number);
  if (!y || !m || !d) return undefined;
  return new Date(y, m - 1, d + plusDays).toISOString();
}
