/** A request that did not work, said plainly. */

export interface ProblemProps {
  error: string | null;
}

export function Problem({ error }: ProblemProps) {
  if (!error) return null;
  return (
    <div className="notice notice-error" role="alert">
      <strong>That did not work.</strong>
      <p>{error}</p>
    </div>
  );
}
