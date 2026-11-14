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
// switches). Phones and computers get the same blob.

const META = { dark: "#000000", light: "#f2f3f5" };
const SWEEP_MS = 1100;

export const currentTheme = () => (document.documentElement.dataset.theme === "light" ? "light" : "dark");
export const motionOn = () => document.documentElement.dataset.motion !== "off";

const changed = () => document.dispatchEvent(new CustomEvent("appearancechange"));

const paintStatusBar = theme => document.querySelector('meta[name="theme-color"]')?.setAttribute("content", META[theme]);

// statusBar=false: leave the phone's status bar alone for now (the pour
// repaints it when it has covered the screen, see setTheme).
function apply(theme, statusBar = true) {
    if (theme === "light") document.documentElement.dataset.theme = "light";
    else delete document.documentElement.dataset.theme;
    if (statusBar) paintStatusBar(theme);
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
// 96 corners keep the edge a curve even when the blob is wider than a big
// monitor; fewer showed as straight segments near the end of the pour, and
// saved little, since the cost is repainting the screen, not the points.
// Phones use it too: they had a plain swaying oval for a while, and the
// owner preferred the liquid edge.
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

// One pour at a time. A second click while one is running used to start a
// new transition, which makes the browser drop the running one mid-pour: the
// screen jumped straight to the end and then poured again (the flicker).
let pouring = false;

export function setTheme(theme, from = null) {
    if (theme === currentTheme() || pouring) return;
    if (!document.startViewTransition || !motionOn()) { apply(theme); return; }
    pouring = true;

    // Where the pour starts, and how far it has to reach to cover the screen.
    const r = from?.getBoundingClientRect();
    const usable = r && r.width > 0;
    const x = usable ? r.left + r.width / 2 : window.innerWidth / 2;
    const y = usable ? r.top + r.height / 2 : 0;
    const reach = Math.hypot(Math.max(x, window.innerWidth - x), Math.max(y, window.innerHeight - y));

    // Worked out before the switch starts, not when the pour has to begin: on
    // a slow phone building ~4,000 points took long enough to cost the first
    // frames of the pour.
    const frames = blobFrames(x, y, reach);

    const root = document.documentElement;
    root.classList.add("theme-switching");
    let sweep = null;
    let done = false;
    const finish = () => {
        if (done) return;
        done = true;
        clearTimeout(watchdog);
        // A finished pour has to be removed, not left holding its last frame.
        // Left in place it stayed attached to the page, and the next switch
        // began with the new theme already covering the whole screen for a
        // frame or two before its own pour took over (the phone glitch).
        sweep?.cancel();
        root.classList.remove("theme-switching");
        paintStatusBar(currentTheme());
        pouring = false;
    };
    // If the browser never reports the switch finished (seen when a phone
    // locks or the tab is put away mid-pour), the toggle must not stay dead.
    const watchdog = setTimeout(finish, SWEEP_MS + 2500);

    // (The new theme starts fully hidden in CSS, so there is no frame where
    // it shows across the whole screen before this animation takes over.)
    const transition = document.startViewTransition(() => apply(theme, false));
    transition.ready.then(() => {
        sweep = root.animate(
            { clipPath: frames },
            { duration: SWEEP_MS, easing: "linear", fill: "both", pseudoElement: "::view-transition-new(root)" },
        );
    }).catch(() => {});
    transition.finished.finally(finish);
}

export function initTheme() {
    label();
    document.getElementById("theme-toggle")?.addEventListener("click", e => {
        setTheme(currentTheme() === "light" ? "dark" : "light", e.currentTarget);
    });
}
