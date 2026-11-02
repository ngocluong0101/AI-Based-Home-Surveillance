import { CircleCheck, CircleX } from "lucide-react";
import type { ConnectionCheckResult } from "../types";

const LABELS: Record<string, string> = {
  connect: "Reachable",
  auth: "Login",
  stream: "Video stream",
  codec: "Codec",
  resolution: "Resolution",
  file: "File",
};

/** The checklist test-connection answers with, one line per check that ran. */
export function ConnectionResult({ result }: { result: ConnectionCheckResult }) {
  return (
    <div className={`conn-result ${result.ok ? "ok" : "bad"}`} role="status">
      <p className="conn-summary">
        {result.ok ? <CircleCheck size={17} /> : <CircleX size={17} />}
        <span>{result.summary}</span>
      </p>
      <ul>
        {result.checks.map((c) => (
          <li key={c.name} className={c.ok ? "ok" : "bad"}>
            {c.ok ? <CircleCheck size={15} /> : <CircleX size={15} />}
            <span>
              <b>{LABELS[c.name] ?? c.name}</b> {c.message}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
