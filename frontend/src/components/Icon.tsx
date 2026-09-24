/** Small, dependency-free line icons shared by the console controls. */
export function Icon({ name }: { name: "history" | "plus" | "settings" | "sun" | "moon" | "close" | "loop" | "play" | "pause" | "next" }) {
  const paths = {
    play: "M7 4l13 8-13 8Z",
    pause: "M8 4v16 M16 4v16",
    next: "M5 4l11 8-11 8Z M19 4v16",
    history: "M3 11a9 9 0 1 1 2 7 M3 4v7h7 M12 7v5l3 2",
    plus: "M12 5v14 M5 12h14",
    settings: "M4 7h16 M4 17h16 M8 4v6 M16 14v6",
    sun: "M12 2v2 M12 20v2 M2 12h2 M20 12h2 M5 5l1.5 1.5 M17.5 17.5L19 19 M5 19l1.5-1.5 M17.5 6.5L19 5 M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0",
    moon: "M20 15a9 9 0 0 1-11-11A9 9 0 1 0 20 15",
    close: "M6 6l12 12 M6 18L18 6",
    loop: "M7 7h10l4 5-4 5H7l-4-5 4-5 M8 12h8 M12 8v8",
  };
  return <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name]} /></svg>;
}
