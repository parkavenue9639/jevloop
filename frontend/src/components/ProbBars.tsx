interface Row {
  key: string;
  label: string;
  value: number;
}

interface Props {
  rows: Row[];
  highlight?: string | null;
  format?: (value: number) => string;
}

/** Horizontal probability bars: one hue, argmax emphasized, values in text ink. */
export function ProbBars({ rows, highlight, format }: Props) {
  const sorted = [...rows].sort((a, b) => b.value - a.value);
  const fmt = format ?? ((v: number) => (v * 100).toFixed(1) + "%");
  return (
    <div className="flex flex-col gap-[6px]">
      {sorted.map((row) => {
        const isPick = row.key === highlight;
        return (
          <div key={row.key} className="flex items-center gap-2">
            <span
              className={`w-44 shrink-0 truncate text-xs ${isPick ? "font-semibold text-ink" : "text-ink2"}`}
              title={row.label}
            >
              {row.label}
            </span>
            <div className="h-2 flex-1 rounded-full bg-surface2">
              <div
                className="h-2 rounded-full bg-accent transition-[width] duration-300"
                style={{ width: `${Math.max(row.value * 100, row.value > 0 ? 1.5 : 0)}%`, opacity: isPick ? 1 : 0.4 }}
              />
            </div>
            <span className={`num w-14 shrink-0 text-right text-xs ${isPick ? "font-semibold text-ink" : "text-ink2"}`}>
              {fmt(row.value)}
            </span>
          </div>
        );
      })}
    </div>
  );
}
