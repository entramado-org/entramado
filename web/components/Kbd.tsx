/** Physical-key chip. Pairs with a small editorial control-panel feel. */
export function Kbd({ children }: { children: React.ReactNode }) {
  return (
    <kbd className="inline-flex h-[18px] min-w-[18px] items-center justify-center rounded-[4px] border border-white/10 bg-ink-700/80 px-1 font-mono text-[10px] leading-none text-slate-300 shadow-[inset_0_-1px_0_rgba(0,0,0,0.45),0_1px_0_rgba(255,255,255,0.03)]">
      {children}
    </kbd>
  );
}
