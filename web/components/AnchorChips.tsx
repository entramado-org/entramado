import { anchorLabel } from "@/lib/graphStyle";

// Non-interactive chips for a briefing's `anchors` (the figures it covers).
// Kept as <span>s, not links — these render inside cards that are already a
// single <Link>, and nesting <a> would be invalid HTML.
export default function AnchorChips({ ids, max = 3 }: { ids?: string[]; max?: number }) {
  if (!ids || ids.length === 0) return null;
  const shown = ids.slice(0, max);
  const extra = ids.length - shown.length;
  return (
    <div className="mt-3 flex flex-wrap gap-1.5">
      {shown.map((id) => {
        const { label, color } = anchorLabel(id);
        return (
          <span
            key={id}
            className="inline-flex items-center gap-1.5 rounded-full border border-white/10 bg-white/5 px-2 py-0.5 text-[10px] text-slate-300"
          >
            <span className="h-1.5 w-1.5 rounded-full" style={{ backgroundColor: color }} />
            {label}
          </span>
        );
      })}
      {extra > 0 && (
        <span className="inline-flex items-center rounded-full border border-white/10 px-2 py-0.5 text-[10px] text-slate-500">
          +{extra}
        </span>
      )}
    </div>
  );
}
