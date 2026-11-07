import { riskBand } from "@/lib/risk";
import { cn } from "@/lib/utils";

const BAND_CLS = ["", "bg-risk-1-bg text-risk-1-fg", "bg-risk-2-bg text-risk-2-fg", "bg-risk-3-bg text-risk-3-fg", "bg-risk-4-bg text-risk-4-fg", "bg-risk-5-bg text-risk-5-fg"];

/** 5x5 likelihood (columns) x severity (rows, 5 at top) matrix with the current cell outlined. Server-renderable. */
export function RiskMatrix({ severity, likelihood, className }: { severity: number; likelihood: number; className?: string }) {
  return (
    <div className={cn("shrink-0", className)} role="img" aria-label={`Risk matrix: severity ${severity}, likelihood ${likelihood}`}>
      <div className="grid grid-cols-[auto_repeat(5,1.6rem)] gap-px text-[0.6rem]">
        {[5, 4, 3, 2, 1].map((s) => (
          <div key={s} className="contents">
            <div className="num pr-1 text-right leading-[1.6rem] text-muted">{s}</div>
            {[1, 2, 3, 4, 5].map((l) => {
              const on = s === severity && l === likelihood;
              return (
                <div
                  key={l}
                  className={cn("num flex h-[1.6rem] items-center justify-center", BAND_CLS[riskBand(s * l)], on && "outline outline-2 -outline-offset-2 outline-ink font-extrabold")}
                >
                  {s * l}
                </div>
              );
            })}
          </div>
        ))}
        <div />
        {[1, 2, 3, 4, 5].map((l) => (
          <div key={l} className="num text-center text-muted">{l}</div>
        ))}
      </div>
      <div className="mt-0.5 text-center text-[0.6rem] text-muted">likelihood &rarr; (rows: severity)</div>
    </div>
  );
}
