import type { Account } from "../api/client";

function fmt(n: number) {
  return n.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
}

export function AccountBar({ account }: { account: Account | null }) {
  if (!account) return <div className="account-bar skeleton">Loading account…</div>;

  const dayPL = account.equity - account.portfolio_value + account.cash;

  return (
    <div className="account-bar">
      <Stat label="Portfolio Value" value={fmt(account.portfolio_value)} />
      <Stat label="Equity" value={fmt(account.equity)} />
      <Stat label="Buying Power" value={fmt(account.buying_power)} />
      <Stat label="Cash" value={fmt(account.cash)} />
      <span className="paper-badge">📄 Paper Trading</span>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="stat">
      <span className="stat-label">{label}</span>
      <span className="stat-value">{value}</span>
    </div>
  );
}
