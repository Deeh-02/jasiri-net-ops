// Motion shared by every page (2026-11-14):
//  1. A highlight that SLIDES to the current item instead of jumping: the
//     sidebar's current page, the underline of a tab bar, the pressed button
//     of a Day/Week/Month toggle.
//  2. Numbers that count up to their value when a page opens, and roll from
//     the old value to the new one when a live refresh changes them.
// The blur-in of a page's content is CSS (.view.is-entering, common.css).
// Everything here is decoration: with animations switched off in Settings
// (theme.js, data-motion="off") nothing moves, and
// if this module failed the app would look the way it did before.

const reduced = () => document.documentElement.dataset.motion === "off";

// ---- 1. Sliding highlight ----

// container selector -> how to find its current item, and the indicator's class
const SLIDERS = [
    { box: ".tabs", current: '.tab.active, .tab.is-active, .tab[aria-selected="true"]', cls: "slide-underline" },
    { box: ".fleet-range, .sr-range", current: 'button[aria-pressed="true"]', cls: "slide-pill" },
    { box: ".sidebar", current: ".nav-link.sub.active", cls: "slide-nav" },
];
const KIND = new WeakMap();        // container -> its SLIDERS entry
const PLACED = new WeakMap();      // container -> the last box we drew ("x,y,w,h") and the width it was drawn at

function kindOf(box) {
    if (!KIND.has(box)) KIND.set(box, SLIDERS.find(s => box.matches(s.box)) || null);
    return KIND.get(box);
}

function indicatorOf(box, kind) {
    let el = [...box.children].find(c => c.classList.contains("slide-indicator"));
    if (!el) {
        el = document.createElement("span");
        el.className = `slide-indicator ${kind.cls}`;
        el.setAttribute("aria-hidden", "true");
        box.append(el);
        box.classList.add("has-slider");
        PLACED.delete(box);
    }
    return el;
}

// Where `item` sits inside `box`, in the box's own scrolling coordinates.
function boxOf(box, item) {
    const b = box.getBoundingClientRect(), r = item.getBoundingClientRect();
    return { x: r.left - b.left + box.scrollLeft - box.clientLeft, y: r.top - b.top + box.scrollTop - box.clientTop, w: r.width, h: r.height };
}

function draw(el, at, animate) {
    el.style.transition = animate && !reduced() ? "" : "none";
    el.style.transform = `translate(${at.x}px, ${at.y}px)`;
    el.style.width = `${at.w}px`;
    el.style.height = `${at.h}px`;
    el.style.opacity = "1";
    if (!animate) { void el.offsetWidth; el.style.transition = ""; }   // the next move animates again
}

// Put the indicator on the current item. It slides when the current item
// changed; it jumps when the container itself was resized, shown or rebuilt.
function place(box, { from = null } = {}) {
    const kind = kindOf(box);
    if (!kind) return;
    const item = [...box.querySelectorAll(kind.current)].find(i => i.offsetParent !== null);
    const el = indicatorOf(box, kind);
    if (!item || !box.offsetWidth) {
        el.style.opacity = "0";
        PLACED.delete(box);
        return;
    }
    const at = boxOf(box, item);
    const key = `${at.x.toFixed(1)},${at.y.toFixed(1)},${at.w.toFixed(1)},${at.h.toFixed(1)}`;
    const before = PLACED.get(box);
    if (before && before.key === key) return;
    if (from) draw(el, boxOf(box, from), false);            // start where the last page's tab was
    const slide = !!from || (!!before && before.width === box.offsetWidth);
    draw(el, at, slide);
    PLACED.set(box, { key, width: box.offsetWidth });
}

const pending = new Set();
let frame = 0;
function schedule(box) {
    pending.add(box);
    if (frame) return;
    frame = requestAnimationFrame(() => {
        frame = 0;
        const boxes = [...pending];
        pending.clear();
        boxes.forEach(b => { if (b.isConnected) place(b); });
    });
}

const ALL = SLIDERS.map(s => s.box).join(", ");
const sizes = typeof ResizeObserver === "function" ? new ResizeObserver(entries => {
    entries.forEach(e => {
        const box = e.target.matches(ALL) ? e.target : e.target.closest(ALL);
        if (!box) return;
        // A fold opening or closing moves the rows under the highlight: follow it, don't lag behind it.
        if (box !== e.target) PLACED.delete(box);
        schedule(box);
    });
}) : null;

function adopt(root) {
    const boxes = root.matches?.(ALL) ? [root] : [];
    root.querySelectorAll?.(ALL).forEach(b => boxes.push(b));
    boxes.forEach(box => {
        if (box.dataset.slider) return;
        box.dataset.slider = "1";
        sizes?.observe(box);
        if (box.matches(".sidebar")) box.querySelectorAll(".nav-subitems").forEach(s => sizes?.observe(s));
        schedule(box);
    });
}

// The tab a page was on when you left it, so the next page's underline can
// start there: Status -> Trends slides across even though each page has its
// own tab bar.
let lastTabs = null;
let ghost = null;                 // a picture of the page being left, and where it was on screen
const tabLabels = bar => [...bar.querySelectorAll(".tab")].map(t => t.textContent.trim().replace(/\s*\d+$/, ""));

export function leavingView(view) {
    ghost = null;
    if (view && !reduced()) {
        // A copy of what's on screen now, to blur away over the new page. No
        // ids or names (they'd clash with the real page), nothing clickable.
        const copy = document.createElement("div");
        copy.className = "view-ghost";
        copy.setAttribute("aria-hidden", "true");
        copy.inert = true;
        [...view.children].forEach(c => { if (!c.hidden && !c.classList.contains("modal-overlay")) copy.append(c.cloneNode(true)); });
        copy.querySelectorAll("[id], [name], [for]").forEach(n => { n.removeAttribute("id"); n.removeAttribute("name"); n.removeAttribute("for"); });
        copy.querySelectorAll(".slide-indicator").forEach(n => n.remove());
        ghost = { copy, rect: view.getBoundingClientRect() };
    }
    const bar = view?.querySelector(":scope > .tabs");
    const kind = bar && kindOf(bar);
    const current = kind && bar.querySelector(kind.current);
    lastTabs = current ? { labels: tabLabels(bar).join("|"), index: [...bar.querySelectorAll(".tab")].indexOf(current) } : null;
}

export function enteringView(view) {
    // The blur-in (CSS) and the first count-up only happen while a page opens,
    // never on a live refresh.
    view.classList.add("is-entering");
    clearTimeout(view._entering);
    view._entering = setTimeout(() => {
        view.classList.remove("is-entering");
        view.style.removeProperty("--enter-delay");
    }, 3200);

    // The old page blurs away where it was; the new one waits for it.
    document.querySelectorAll(".view-ghost").forEach(g => g.remove());
    const host = view.parentElement;
    if (ghost && host) {
        const at = host.getBoundingClientRect();
        if (getComputedStyle(host).position === "static") host.style.position = "relative";
        const { copy, rect } = ghost;
        copy.style.top = `${rect.top - at.top + host.scrollTop}px`;
        copy.style.left = `${rect.left - at.left}px`;
        copy.style.width = `${rect.width}px`;
        copy.style.right = "auto";
        // Only what was on screen: a long table below the fold isn't worth drawing.
        copy.style.maxHeight = `${Math.max(0, window.innerHeight - rect.top) + 40}px`;
        copy.style.overflow = "hidden";
        host.append(copy);
        const done = () => copy.remove();
        copy.addEventListener("animationend", done, { once: true });
        setTimeout(done, 600);
        view.style.setProperty("--enter-delay", "0.2s");
        enterDelay = 200;
    } else {
        enterDelay = 0;
    }
    ghost = null;

    // Its figures count up on every visit, including ones still on screen
    // from last time (the page won't redraw a figure that hasn't changed).
    view.querySelectorAll(COUNTED).forEach(f => {
        SHOWN.delete(f);
        if (f.offsetParent !== null) noticed(f);
    });

    adopt(view);
    const bar = view.querySelector(":scope > .tabs");
    if (bar && lastTabs && tabLabels(bar).join("|") === lastTabs.labels) {
        const from = bar.querySelectorAll(".tab")[lastTabs.index];
        const kind = kindOf(bar), current = bar.querySelector(kind.current);
        if (from && current && from !== current) {
            PLACED.delete(bar);
            place(bar, { from });
        }
    }
    lastTabs = null;
}

// ---- 2. Counting numbers ----

const COUNTED = ".stat-value, .home-kpi-value, .home-figure > b, .rev-card-value, .rev-tile-value, .summary-item > strong, .rev-attention-count, " +
    ".fleet-kpi-figure, .sr-kpi-figure, .status-readout-value, .olt-signal .olt-dbm";
let enterDelay = 0;               // ms the new page waits for the old one to blur away
const NUMBER = /^(\D*?)(\d[\d,]*)(\.\d+)?(\D*)$/s;
const SHOWN = new WeakMap();       // element -> the text the page last gave it
const ROLLING = new WeakMap();     // element -> the frame id of its roll

function parse(text) {
    const m = NUMBER.exec(text);
    if (!m) return null;
    const value = Number(m[2].replace(/,/g, "") + (m[3] || ""));
    if (!Number.isFinite(value)) return null;
    return { pre: m[1], post: m[4], value, decimals: m[3] ? m[3].length - 1 : 0, commas: m[2].includes(",") || m[2].length < 4 };
}

function format(n, shape) {
    const fixed = n.toFixed(shape.decimals);
    if (!shape.commas) return shape.pre + fixed + shape.post;
    const [whole, frac] = fixed.split(".");
    return shape.pre + whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",") + (frac ? `.${frac}` : "") + shape.post;
}

// Roll `el` from one number to the text the page just set. The page's own
// text is always what's left at the end, exactly as it wrote it.
function roll(el, final, fromValue, wait = 0) {
    const shape = parse(final);
    cancelAnimationFrame(ROLLING.get(el));
    if (!shape || reduced() || shape.value === fromValue || document.hidden) return;
    const start = performance.now() + wait, length = wait ? 1300 : 800;
    let written = final;
    const step = now => {
        if (!el.isConnected || el.textContent !== written) return;     // the page wrote something newer: leave it
        const t = Math.max(0, Math.min(1, (now - start) / length));
        const eased = 1 - Math.pow(1 - t, 3);
        written = t === 1 ? final : format(fromValue + (shape.value - fromValue) * eased, shape);
        el.textContent = written;
        SHOWN.set(el, final);
        counts?.takeRecords();                                          // our own write isn't news
        if (t < 1) ROLLING.set(el, requestAnimationFrame(step));
    };
    step(start);
}

function noticed(el) {
    if (el.childElementCount) return;                // only plain-text figures
    const text = el.textContent;
    const before = SHOWN.get(el);
    if (before === text) return;
    SHOWN.set(el, text);
    const view = el.closest(".view");
    if (before === undefined) {
        // First time on screen: count up from zero, but only while its page is opening.
        if (view?.classList.contains("is-entering")) roll(el, text, 0, enterDelay + 150);
        return;
    }
    const old = parse(before);
    if (old) roll(el, text, old.value);
}

let counts = null;

// ---- Wiring ----

export function initMotion() {
    adopt(document.body);
    const sliderAttrs = new Set(["class", "aria-pressed", "aria-selected", "hidden"]);
    counts = new MutationObserver(records => {
        const figures = new Set();
        records.forEach(r => {
            const target = r.target.nodeType === 1 ? r.target : r.target.parentElement;
            if (!target) return;
            if (r.type === "attributes") {
                if (sliderAttrs.has(r.attributeName) && !target.classList.contains("slide-indicator")) {
                    const box = target.closest(ALL);
                    if (box) schedule(box);
                }
                return;
            }
            // Something was drawn or redrawn.
            r.addedNodes.forEach(n => {
                if (n.nodeType !== 1 || n.classList.contains("slide-indicator")) return;
                adopt(n);
                if (n.matches(COUNTED)) figures.add(n);
                n.querySelectorAll(COUNTED).forEach(f => figures.add(f));
            });
            const box = target.closest(ALL);
            if (box && !target.classList.contains("slide-indicator")) schedule(box);
            const figure = target.closest(COUNTED);
            if (figure) figures.add(figure);
        });
        figures.forEach(noticed);
    });
    counts.observe(document.body, {
        subtree: true, childList: true, characterData: true,
        attributes: true, attributeFilter: [...sliderAttrs],
    });
    window.addEventListener("resize", () => document.querySelectorAll(ALL).forEach(b => { PLACED.delete(b); schedule(b); }));
    document.fonts?.ready?.then(() => document.querySelectorAll(ALL).forEach(b => { PLACED.delete(b); schedule(b); }));
}
