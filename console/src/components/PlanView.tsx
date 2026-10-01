/**
 * The planner's estimate, node by node -- and, after Analyze, what happened.
 *
 * The root's total cost is the number the agent's planner gate compares
 * against MAX_PLAN_COST, so it is the first thing shown. Below it the tree,
 * indented, with each node's own estimate; after Analyze, the actual time,
 * rows and loops beside it, which is where an estimate of one row that was
 * really a million shows itself. The raw document is one click away for
 * anything the table leaves out.
 */

import { flattenPlan } from "../api/plan";
import { formatNumber } from "../api/format";

export interface PlanViewProps {
  plan: unknown;
  maxPlanCost: number;
}

function value(number: number | null, digits = 2): string {
  return number === null ? "" : formatNumber(number, digits);
}

export function PlanView({ plan, maxPlanCost }: PlanViewProps) {
  const flat = flattenPlan(plan);
  if (flat.nodes.length === 0) {
    return <p className="muted">No plan: the query did not get as far as the planner.</p>;
  }
  const { analyzed } = flat;

  return (
    <section className="plan" aria-label="Plan">
      <p className="small">
        Estimated total cost <strong>{value(flat.nodes[0]!.totalCost)}</strong> against the agent's ceiling of{" "}
        {formatNumber(maxPlanCost, 0)}
        {flat.planningTime !== null && <> · planning {value(flat.planningTime, 3)} ms</>}
        {flat.executionTime !== null && <> · execution {value(flat.executionTime, 3)} ms</>}
      </p>
      <div className="table-scroll">
        <table className="grid plan-grid">
          <thead>
            <tr>
              <th scope="col">Node</th>
              <th scope="col" className="num">
                Est. rows
              </th>
              <th scope="col" className="num">
                Cost
              </th>
              {analyzed && (
                <>
                  <th scope="col" className="num">
                    Actual rows
                  </th>
                  <th scope="col" className="num">
                    Loops
                  </th>
                  <th scope="col" className="num">
                    Time (ms)
                  </th>
                </>
              )}
            </tr>
          </thead>
          <tbody>
            {flat.nodes.map((node, index) => (
              <tr key={index}>
                <td style={{ paddingLeft: `${0.6 + node.depth * 1.2}rem` }}>
                  <span className="plan-node">{node.node}</span>
                  {node.target && <span className="mono small"> {node.target}</span>}
                  {node.detail.map((line) => (
                    <div className="mono small muted" key={line}>
                      {line}
                    </div>
                  ))}
                </td>
                <td className="num">{value(node.planRows, 0)}</td>
                <td className="num">
                  {value(node.startupCost)}..{value(node.totalCost)}
                </td>
                {analyzed && (
                  <>
                    <td className="num">{value(node.actualRows, 0)}</td>
                    <td className="num">{value(node.loops, 0)}</td>
                    <td className="num">{value(node.actualTime, 3)}</td>
                  </>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <details className="raw">
        <summary className="small">The plan as Postgres wrote it</summary>
        <pre className="code">{JSON.stringify(plan, null, 2)}</pre>
      </details>
    </section>
  );
}
