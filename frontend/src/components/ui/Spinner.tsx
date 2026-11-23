/*
 * @author: Ngoc Luong <ngocluongg0101@gmail.com>
 */

export function Spinner({ label = "Loading" }: { label?: string }) {
  return <span className="spinner" role="status" aria-label={label} />;
}
