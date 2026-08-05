/** Relative time string from ISO date */
export function relTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const diff = Date.now() - new Date(iso).getTime();
  const abs = Math.abs(diff);
  const future = diff < 0;
  const prefix = future ? "in " : "";
  const suffix = future ? "" : " ago";

  if (abs < 60_000) return "just now";
  if (abs < 3_600_000) {
    const m = Math.floor(abs / 60_000);
    return `${prefix}${m}m${suffix}`;
  }
  if (abs < 86_400_000) {
    const h = Math.floor(abs / 3_600_000);
    return `${prefix}${h}h${suffix}`;
  }
  const d = Math.floor(abs / 86_400_000);
  return `${prefix}${d}d${suffix}`;
}

/** Format date as "Mon 15, 02:30 PM" */
export function shortDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "—";
  return d.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** Combine class names, filtering falsy values */
export function cn(...classes: (string | false | null | undefined)[]): string {
  return classes.filter(Boolean).join(" ");
}
