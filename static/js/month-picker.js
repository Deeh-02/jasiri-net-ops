// A month picker you can type into or pick from. The browser's own
// <input type="month"> is a plain text box in some browsers, so this works the
// same everywhere: a box that takes "2026-11", "nov 2026" or "11/2026", and a
// calendar button that opens the year's twelve months.
//
//   const from = monthPicker(document.getElementById("from"), { label: "From", max: "2026-11", onChange: v => ... });
//   from.value            // "2026-11" (YYYY-MM), "" when empty
//   from.set("2026-05")   // changes the box without calling onChange

const NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const LONG = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"];

const ym = (y, m) => `${y}-${String(m).padStart(2, "0")}`;
const show = value => (value ? `${NAMES[Number(value.slice(5)) - 1]} ${value.slice(0, 4)}` : "");

// "2026-11", "2026/11", "11/2026", "nov 2026", "November 2026", "nov26" -> "2026-11"; null if it isn't a month.
export function parseMonth(text) {
    const t = String(text).trim().toLowerCase().replace(/[.,]/g, " ").replace(/\s+/g, " ");
    let m, y;
    let hit = t.match(/^(\d{4})[-/ ](\d{1,2})$/);
    if (hit) { y = +hit[1]; m = +hit[2]; }
    else if ((hit = t.match(/^(\d{1,2})[-/ ](\d{4})$/))) { m = +hit[1]; y = +hit[2]; }
    else if ((hit = t.match(/^([a-z]{3,9}) ?(\d{2}|\d{4})$/))) {
        m = LONG.findIndex(name => name.startsWith(hit[1]) && hit[1].length >= 3) + 1;
        y = +hit[2] < 100 ? 2000 + +hit[2] : +hit[2];
    }
    return m >= 1 && m <= 12 && y >= 2000 && y <= 2100 ? ym(y, m) : null;
}

export function monthPicker(root, { label, max = null, min = null, onChange = () => {} }) {
    let value = "", year = new Date().getFullYear();
    root.classList.add("mp");
    root.innerHTML = `<input class="control mp-input" type="text" autocomplete="off" spellcheck="false" placeholder="e.g. Nov 2026" aria-label="${label}">
        <button type="button" class="mp-btn" aria-label="${label}: pick a month" aria-haspopup="dialog" aria-expanded="false">
            <svg viewBox="0 0 20 20" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><rect x="3" y="4.5" width="14" height="12" rx="2"/><path d="M3 8h14M7 3v3M13 3v3"/></svg>
        </button>
        <div class="mp-pop" role="dialog" aria-label="${label}" hidden>
            <div class="mp-year"><button type="button" class="mp-nav" data-step="-1" aria-label="Previous year">&#8249;</button><b></b><button type="button" class="mp-nav" data-step="1" aria-label="Next year">&#8250;</button></div>
            <div class="mp-grid"></div>
        </div>`;
    const input = root.querySelector(".mp-input"), button = root.querySelector(".mp-btn"), pop = root.querySelector(".mp-pop");
    const allowed = v => (!max || v <= max) && (!min || v >= min);

    function draw() {
        pop.querySelector("b").textContent = year;
        pop.querySelector('[data-step="1"]').disabled = !!max && year >= Number(max.slice(0, 4));
        pop.querySelector('[data-step="-1"]').disabled = !!min && year <= Number(min.slice(0, 4));
        pop.querySelector(".mp-grid").innerHTML = NAMES.map((name, i) => {
            const v = ym(year, i + 1);
            return `<button type="button" class="mp-month${v === value ? " is-on" : ""}" data-month="${v}"${allowed(v) ? "" : " disabled"}>${name}</button>`;
        }).join("");
    }
    function close() { pop.hidden = true; button.setAttribute("aria-expanded", "false"); }
    function open() { year = value ? Number(value.slice(0, 4)) : year; draw(); pop.hidden = false; button.setAttribute("aria-expanded", "true"); }
    function set(v) { value = v || ""; input.value = show(value); input.classList.remove("is-bad"); }
    function choose(v) { if (!allowed(v)) return false; const changed = v !== value; set(v); close(); if (changed) onChange(v); return true; }

    button.addEventListener("click", () => (pop.hidden ? open() : close()));
    pop.addEventListener("click", (e) => {
        const nav = e.target.closest(".mp-nav"), month = e.target.closest(".mp-month");
        if (nav) { year += Number(nav.dataset.step); draw(); }
        if (month) choose(month.dataset.month);
    });
    // Typing: take it when it reads as a month, else put the old value back.
    const commit = () => {
        const v = parseMonth(input.value);
        if (v && allowed(v)) choose(v);
        else { input.classList.toggle("is-bad", !!input.value.trim() && !v); set(value); }
    };
    input.addEventListener("change", commit);
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); commit(); } });
    root.addEventListener("keydown", (e) => { if (e.key === "Escape" && !pop.hidden) { close(); button.focus(); } });
    document.addEventListener("click", (e) => { if (!root.contains(e.target)) close(); });

    return { get value() { return value; }, set, setLimits(next) { max = next.max ?? max; min = next.min ?? min; } };
}
