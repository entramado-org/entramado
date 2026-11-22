"use client";

import { useRef, useState, type PointerEvent as ReactPointerEvent } from "react";

/** Shared chrome for the mobile detail bottom-sheets (NodeDetailPanel /
 *  EdgeDetailPanel). On phones the sheet snaps between three heights so the
 *  graph below stays usable; desktop renders the same panels as a static side
 *  rail and ignores all of this. Kept here so both panels never drift apart. */

export type SheetSnap = "peek" | "half" | "full";

/** Tap order when cycling the grab handle. */
export const nextSnap: Record<SheetSnap, SheetSnap> = {
  peek: "half",
  half: "full",
  full: "peek",
};

/** Mobile-only height per snap. `peek` = header bar only (h-auto); the body is
 *  hidden by the panel when peek. dvh (not vh) so the mobile URL bar is honoured.
 *  Desktop height comes from the panel's own `md:h-full`, so no md: variant here. */
export const SNAP_HEIGHT: Record<SheetSnap, string> = {
  peek: "h-auto",
  half: "h-[50dvh]",
  full: "h-[85dvh]",
};

/** Fraction of the viewport the sheet covers from the bottom — fed to the canvas
 *  so a focused node is nudged up above the sheet. peek leaves a sliver only. */
export const coverFrac: Record<SheetSnap, number> = {
  peek: 0.12,
  half: 0.5,
  full: 0.85,
};

/** Snap whose coverFrac is closest to a (0..1) viewport fraction — used to
 *  resolve where a drag lands when the finger is released. */
function nearestSnap(frac: number): SheetSnap {
  let best: SheetSnap = "half";
  let bestD = Infinity;
  for (const s of ["peek", "half", "full"] as SheetSnap[]) {
    const d = Math.abs(frac - coverFrac[s]);
    if (d < bestD) {
      bestD = d;
      best = s;
    }
  }
  return best;
}

/** Drag-to-resize for the mobile sheet. Returns a live `dragPx` height while the
 *  finger is down (null otherwise — the sheet then falls back to its SNAP_HEIGHT
 *  class) plus pointer handlers to spread on the grab handle. A movement under
 *  the threshold counts as a tap and cycles the snap, preserving the old
 *  tap-to-cycle UX. Desktop never wires this in (the handle is md:hidden). */
export function useSheetDrag(snap: SheetSnap, onSnapChange: (s: SheetSnap) => void) {
  const [dragPx, setDragPx] = useState<number | null>(null);
  const start = useRef<{ y: number; moved: boolean } | null>(null);

  const onPointerDown = (e: ReactPointerEvent) => {
    start.current = { y: e.clientY, moved: false };
    try {
      e.currentTarget.setPointerCapture(e.pointerId);
    } catch {
      /* pointer capture unavailable (e.g. synthetic events) — drag still works */
    }
  };
  const onPointerMove = (e: ReactPointerEvent) => {
    const s = start.current;
    if (!s) return;
    if (Math.abs(e.clientY - s.y) > 6) s.moved = true;
    const vh = window.innerHeight;
    const h = vh - e.clientY; // sheet is bottom-anchored
    setDragPx(Math.max(120, Math.min(vh * 0.92, h)));
  };
  const onPointerUp = (e: ReactPointerEvent) => {
    const s = start.current;
    start.current = null;
    try {
      e.currentTarget.releasePointerCapture(e.pointerId);
    } catch {
      /* pointer already released */
    }
    if (!s) return;
    if (!s.moved) {
      onSnapChange(nextSnap[snap]); // tap → cycle
    } else if (dragPx != null) {
      onSnapChange(nearestSnap(dragPx / window.innerHeight)); // drag → snap to nearest
    }
    setDragPx(null);
  };

  return {
    dragPx,
    handlers: { onPointerDown, onPointerMove, onPointerUp, onPointerCancel: onPointerUp },
  };
}

/** The grab handle pill at the top of a mobile sheet. Tapping it cycles the snap
 *  height; dragging it resizes the sheet (see useSheetDrag). A 44px-tall tap
 *  target; hidden on desktop where the side rail has no snap behaviour. */
export function SheetHandle({
  handlers,
}: {
  handlers: ReturnType<typeof useSheetDrag>["handlers"];
}) {
  return (
    <button
      {...handlers}
      className="flex h-11 w-full shrink-0 touch-none items-center justify-center md:hidden"
      aria-label="Arrastrar o tocar para cambiar la altura del panel"
      title="Arrastrar o tocar para cambiar la altura"
    >
      <span className="h-1.5 w-10 rounded-full bg-white/25" />
    </button>
  );
}
