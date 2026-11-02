import { percent } from "../utils/format";

export function ConfidenceBar({ value }: { value: number }) {
  return (
    <div className="conf" title={`Confidence ${percent(value)}`}>
      <div className="bar-track">
        <div className="bar-fill" style={{ width: percent(value) }} />
      </div>
      <span>{percent(value)}</span>
    </div>
  );
}
