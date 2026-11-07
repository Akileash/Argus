/** Risk matrix helpers (likelihood x severity = 1..25). Band => token suffix in globals.css (risk-N-bg/fg). */
export type RiskBand = 1 | 2 | 3 | 4 | 5;

export function riskBand(score: number): RiskBand {
  if (score >= 20) return 5;
  if (score >= 15) return 4;
  if (score >= 10) return 3;
  if (score >= 5) return 2;
  return 1;
}

export const RISK_LABELS: Record<RiskBand, string> = {
  1: "Low",
  2: "Moderate-low",
  3: "Moderate",
  4: "High",
  5: "Extreme",
};
