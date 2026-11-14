// Appearance (2026-11-14): light or dark, and animations on or off. Both are
// remembered per browser (localStorage "theme" and "motion"), and index.html
// applies both before the first paint.
//
// Theme: data-theme="light" on <html> (dark has no attribute); the colours
// themselves are the two token sets at the top of common.css.
// Animations: ON unless the person turns them off (data-motion="off" on
// <html>; common.css and motion.js read it). The device's own "reduce motion"
// setting is deliberately not followed: owner's call, see common.css.
//
// Switching theme pours the new one across the screen from the button: a blob
// with a soft, wobbling edge that grows until it covers the page (the
// browser's View Transitions; without them, or with animations off, it just
// switches).

const META = { dark: "#000000", light: "#f2f3f5" };
const SWEEP_MS = 1100;

export const currentTheme = () => (document.documentElement.dataset.theme === "light" ? "light" : "dark");
export const motionOn = () => document.documentElement.dataset.motion !== "off";

const changed = () => document.dispatchEvent(new CustomEvent("appearancechange"));

function apply(theme) {
    if (theme === "light") document.documentElement.dataset.theme = "light";
    else delete document.documentElement.dataset.theme;
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content", META[theme]);
    try { localStorage.setItem("theme", theme); } catch (e) { /* private mode: lasts for this visit */ }
    label();
    changed();
}

export function setMotion(on) {
    if (on) delete document.documentElement.dataset.motion;
    else document.documentElement.dataset.motion = "off";
    try { localStorage.setItem("motion", on ? "on" : "off"); } catch (e) { /* private mode: lasts for this visit */ }
    changed();
}

function label() {
    const btn = document.getElementById("theme-toggle");
    if (!btn) return;
    const next = currentTheme() === "light" ? "dark" : "light";
    btn.title = `Switch to ${next} mode`;
    btn.setAttribute("aria-label", btn.title);
    btn.setAttribute("aria-pressed", String(currentTheme() === "light"));
}

// The blob's outline at each step of the sweep, as clip-path polygons with
// the same number of corners so the browser can blend one into the next.
// Three slow waves of different lengths travel round the edge while it grows,
// which is what makes it read as liquid instead of a compass-drawn circle.
// `reach` is the distance to the farthest corner of the screen; the last
// outline is past it on every side, so the new theme always ends up covering
// everything.
export function blobFrames(x, y, reach, steps = 44, corners = 96) {
    const WOBBLE = 0.2;
    const waves = [[2, 0.55, 0.9, 2.4], [3, 0.3, 2.1, -1.7], [5, 0.15, 4.0, 3.1]];   // [lobes, share, phase, drift]
    const full = (reach * 1.03) / (1 - WOBBLE);
    const frames = [];
    for (let s = 0; s <= steps; s++) {
        const t = s / steps;
        const radius = full * (0.12 * t + 0.88 * Math.pow(t, 2.3));   // lingers small at the button, then pours
        const points = [];
        for (let c = 0; c < corners; c++) {
            const a = (c / corners) * Math.PI * 2;
            let wave = 0;
            for (const [lobes, share, phase, drift] of waves) wave += share * Math.sin(lobes * a + phase + drift * t);
            const r = radius * (1 + WOBBLE * wave);
            points.push(`${(x + r * Math.cos(a)).toFixed(1)}px ${(y + r * Math.sin(a)).toFixed(1)}px`);
        }
        frames.push(`polygon(${points.join(", ")})`);
    }
    return frames;
}

export function setTheme(theme, from = null) {
    if (theme === currentTheme()) return;
    if (!document.startViewTransition || !motionOn()) { apply(theme); return; }

    // Where the pour starts, and how far it has to reach to cover the screen.
    const r = from?.getBoundingClientRect();
    const usable = r && r.width > 0;
    const x = usable ? r.left + r.width / 2 : window.innerWidth / 2;
    const y = usable ? r.top + r.height / 2 : 0;
    const reach = Math.hypot(Math.max(x, window.innerWidth - x), Math.max(y, window.innerHeight - y));

    const root = document.documentElement;
    root.classList.add("theme-switching");
    const transition = document.startViewTransition(() => apply(theme));
    transition.ready.then(() => {
        root.animate(
            { clipPath: blobFrames(x, y, reach) },
            { duration: SWEEP_MS, easing: "linear", fill: "both", pseudoElement: "::view-transition-new(root)" },
        );
    }).catch(() => {});
    transition.finished.finally(() => root.classList.remove("theme-switching"));
}

export function initTheme() {
    label();
    document.getElementById("theme-toggle")?.addEventListener("click", e => {
        setTheme(currentTheme() === "light" ? "dark" : "light", e.currentTarget);
    });
}
