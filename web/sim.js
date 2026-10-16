// Course simulation tab: one makerspace course per programme-year over academic year 2027/28,
// with project groups as pieces filling the makerspace's weekly slots. ALL ACTIVITIES ARE MOCK
// EXAMPLES attached to real courses (see data/sim_courses.yaml).
// SimCore is pure (no DOM) and is exercised by tests/sim.test.cjs; SimUI renders the tab.

const SimCore = (() => {
  const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri"];
  const HALF = DAYS.flatMap(d => [d + " am", d + " pm"]);               // 10 daytime slots
  const OPEN_ROWS = ["Mon 17–21", "Tue 17–21", "Wed 17–21", "Thu 17–21", "Fri 17–21", "Sat 10–14"];
  const OPEN_ORDER = [0, 1, 2, 3, 5, 4];      // which evenings get staffed first: Mon-Thu, Sat, Fri

  function mulberry32(seed) {
    let a = seed >>> 0;
    return () => {
      a = (a + 0x6d2b79f5) >>> 0;
      let t = a;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  function hash(s) { let h = 2166136261; for (const ch of s) { h ^= ch.charCodeAt(0); h = Math.imul(h, 16777619); } return h >>> 0; }
  function shuffle(a, r) { for (let i = a.length - 1; i > 0; i--) { const j = Math.floor(r() * (i + 1)); [a[i], a[j]] = [a[j], a[i]]; } return a; }

  // Participants: explicit course `students`, else the sum over takers (share of cohort or head count).
  function participants(course, cohortSize) {
    if (course.students != null) return Math.max(0, Math.round(course.students));
    return Math.round((course.takers || []).reduce((s, t) =>
      s + (t.students != null ? t.students : (t.share ?? 1) * (cohortSize[t.cohort] || 0)), 0));
  }

  // Per-course derived data (groups with their sizes, sections, owned half-days), seeded per
  // course so that editing one course does not reshuffle the others.
  function prepare(course, rules, seed) {
    const r = mulberry32(seed ^ hash(course.id));
    const n = Math.max(1, course.n_students);
    const gs = Math.max(1, Math.min(10, Math.round(course.group_size || 4)));
    const groups = Math.ceil(n / gs);
    const sizes = Array.from({ length: groups }, (_, g) => (g < groups - 1 ? gs : n - gs * (groups - 1)));
    const perSec = Math.max(1, Math.floor(rules.section_students / gs));
    const sections = [];
    for (let g = 0; g < groups; g += perSec) sections.push(Array.from({ length: Math.min(perSec, groups - g) }, (_, k) => g + k));
    let own;
    if (course.own && course.own.length) own = course.own.map(s => HALF.indexOf(s)).filter(i => i >= 0);
    else {
      const k = course.own_n || rules.own_n[course.level] || 2;
      own = shuffle(HALF.map((_, i) => i), r).slice(0, k).sort((a, b) => a - b);
    }
    return { groups, sizes, sections, own, gs };
  }

  function schedule(courses, rules, seed) {
    const W = rules.teaching_weeks;
    const out = { periods: {}, results: [] };
    let eid = 0;
    const prep = courses.map(c => prepare(c, rules, seed));
    const sessionsNeeded = c => Math.ceil(c.sup_h / rules.session_h);
    const visitsNeeded = c => Math.ceil(c.unsup_h / rules.visit_h);
    const res = courses.map((c, i) => ({ id: c.id, students: c.n_students, groups: prep[i].groups, sizes: prep[i].sizes,
      sections: prep[i].sections.length, own: prep[i].own,
      supReq: sessionsNeeded(c) * rules.session_h, unsupReq: visitsNeeded(c) * rules.visit_h,
      supSessions: 0, groupSupSessions: 0, groupVisits: 0, supShort: 0, unsupShort: 0 }));

    for (const per of ["P1", "P2", "P3", "P4"]) {
      const r = mulberry32(seed ^ hash(per));
      const day = Array.from({ length: W }, () => HALF.map(() => ({
        cap: rules.day_cap_persons, used: 0, blocked: r() >= rules.day_available, events: [] })));
      const openSet = new Set(OPEN_ORDER.slice(0, rules.open_slots_per_week));
      const open = Array.from({ length: W }, () => OPEN_ROWS.map((_, j) => ({
        cap: rules.open_cap_persons, used: 0, open: openSet.has(j), events: [] })));
      const events = [];
      const idx = courses.map((c, i) => i).filter(i => courses[i].period === per);
      const win = i => [courses[i].weeks[0] - 1, Math.min(courses[i].weeks[1], W) - 1];

      // Supervised: whole sections (groups + a teacher) in the course's own daytime half-days
      const secs = [];
      for (const i of idx) prep[i].sections.forEach(g => {
        const [w0, w1] = win(i);
        secs.push({ c: i, groups: g, persons: g.reduce((s, k) => s + prep[i].sizes[k], 0) + rules.teachers_per_section,
          need: sessionsNeeded(courses[i]), w0, w1 });
      });
      // Unsupervised: single groups (up to visit_members people) in staffed evening/Saturday slots
      const grs = [];
      for (const i of idx) for (let g = 0; g < prep[i].groups; g++) {
        const [w0, w1] = win(i);
        grs.push({ c: i, g, persons: Math.min(rules.visit_members, prep[i].sizes[g]), need: visitsNeeded(courses[i]), w0, w1 });
      }

      for (let w = 0; w < W; w++) {
        const act = secs.filter(x => x.need > 0 && w >= x.w0 && w <= x.w1);
        act.forEach(x => { x.quota = Math.min(rules.max_sessions_per_week, Math.ceil(x.need / (x.w1 - w + 1))); x.done = 0; x.used = new Set(); });
        for (let round = 0; round < rules.max_sessions_per_week; round++) {
          const order = shuffle(act.slice(), r).sort((a, b) => b.need / (b.w1 - w + 1) - a.need / (a.w1 - w + 1));
          for (const x of order) {
            if (x.done >= x.quota || x.need === 0) continue;
            let best = -1, bv = -1;
            for (const s of prep[x.c].own) {
              const cell = day[w][s];
              const free = cell.cap - cell.used;
              if (cell.blocked || x.used.has(s) || free < x.persons) continue;
              const v = free + r() * 0.5;
              if (v > bv) { bv = v; best = s; }
            }
            if (best < 0) continue;
            const cell = day[w][best];
            cell.used += x.persons;
            const ev = { id: eid++, c: x.c, kind: "sup", w, slot: best, groups: x.groups, teachers: rules.teachers_per_section };
            cell.events.push(ev); events.push(ev);
            x.need--; x.done++; x.used.add(best);
            res[x.c].supSessions++; res[x.c].groupSupSessions += x.groups.length;
          }
        }
        const ga = grs.filter(x => x.need > 0 && w >= x.w0 && w <= x.w1);
        ga.forEach(x => { x.quota = Math.ceil(x.need / (x.w1 - w + 1)); x.done = 0; x.used = new Set(); });
        for (let round = 0; round < 2; round++) {
          const order = shuffle(ga.slice(), r).sort((a, b) => b.need / (b.w1 - w + 1) - a.need / (a.w1 - w + 1));
          for (const x of order) {
            if (x.done >= x.quota || x.need === 0) continue;
            let best = -1, bv = -1;
            open[w].forEach((cell, j) => {
              if (!cell.open || x.used.has(j) || cell.cap - cell.used < x.persons) return;
              const v = cell.cap - cell.used + r() * 0.5;
              if (v > bv) { bv = v; best = j; }
            });
            if (best < 0) continue;
            const cell = open[w][best];
            cell.used += x.persons;
            const ev = { id: eid++, c: x.c, kind: "unsup", w, slot: best, groups: [x.g], persons: x.persons };
            cell.events.push(ev); events.push(ev);
            x.need--; x.done++; x.used.add(best);
            res[x.c].groupVisits++;
          }
        }
      }
      for (const x of secs) res[x.c].supShort += x.need * x.groups.length;
      for (const x of grs) res[x.c].unsupShort += x.need;

      const dayCap = day.flat().filter(c => !c.blocked).reduce((s, c) => s + c.cap, 0);
      const dayUsed = day.flat().reduce((s, c) => s + c.used, 0);
      const openCap = open.flat().filter(c => c.open).reduce((s, c) => s + c.cap, 0);
      const openUsed = open.flat().reduce((s, c) => s + c.used, 0);
      out.periods[per] = { day, open, events, courses: idx, dayUtil: dayCap ? dayUsed / dayCap : 0, openUtil: openCap ? openUsed / openCap : 0 };
    }
    out.results = res.map(x => ({ ...x,
      supGot: x.groupSupSessions / x.groups * rules.session_h,
      unsupGot: x.groupVisits / x.groups * rules.visit_h }));
    return out;
  }

  return { DAYS, HALF, OPEN_ROWS, mulberry32, hash, participants, prepare, schedule };
})();

const SimUI = (() => {
  const COLORS = ["#004791", "#E86A58", "#4DA060", "#FFBE00", "#6298D2", "#A65900", "#339C9C", "#78001A", "#0D4A21", "#000061", "#B2E0E0"];
  const PERIODS = ["P1", "P2", "P3", "P4"];
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const UNITS = 10;                 // person-units per board row
  const MAX_PIECES = 60;            // illustration is cut into at most this many pieces
  const reduceMotion = () => typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;

  // ---- Illustrations: simple line drawings in a 150 x 100 box; `c` is the course colour.
  const S = 'stroke="var(--ink)" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"';
  const M = {
    turbine: c => `<line x1="75" y1="45" x2="75" y2="92" ${S}/><circle cx="75" cy="45" r="6" fill="${c}" ${S}/><path d="M75 45 L70 8 Q78 6 80 12 Z M75 45 L108 63 Q104 71 98 68 Z M75 45 L42 63 Q40 55 46 53 Z" fill="${c}" ${S}/><line x1="55" y1="92" x2="95" y2="92" ${S}/>`,
    spectrometer: c => `<rect x="18" y="38" width="62" height="30" rx="4" fill="none" ${S}/><line x1="80" y1="53" x2="98" y2="53" ${S}/><path d="M98 53 L138 30 M98 53 L140 44 M98 53 L140 58 M98 53 L138 72" stroke="${c}" stroke-width="3"/><circle cx="12" cy="53" r="5" fill="${c}" ${S}/>`,
    bridge: c => `<path d="M10 70 L140 70 M10 70 L30 40 L50 70 L70 40 L90 70 L110 40 L130 70 M30 40 L130 40" fill="none" ${S}/><path d="M10 70 L10 88 M140 70 L140 88" ${S}/><path d="M64 70 L75 84 L86 70" fill="${c}" ${S}/>`,
    detector: c => `<rect x="44" y="36" width="44" height="44" rx="6" fill="${c}" ${S}/><path d="M22 26 Q30 34 22 42 M14 20 Q26 34 14 48 M110 50 L134 50 M118 42 L134 42 M118 58 L134 58" fill="none" ${S}/><circle cx="66" cy="58" r="8" fill="var(--sheet)" ${S}/>`,
    pendulum: c => `<line x1="30" y1="12" x2="120" y2="12" ${S}/><line x1="75" y1="12" x2="60" y2="50" ${S}/><circle cx="60" cy="50" r="7" fill="${c}" ${S}/><line x1="60" y1="50" x2="88" y2="78" ${S}/><circle cx="88" cy="78" r="8" fill="${c}" ${S}/><path d="M100 86 Q112 70 104 54" fill="none" stroke="${c}" stroke-width="2" stroke-dasharray="3 4"/>`,
    speaker: c => `<path d="M22 40 L40 40 L62 22 L62 78 L40 60 L22 60 Z" fill="${c}" ${S}/><path d="M76 34 Q88 50 76 66 M88 26 Q106 50 88 74 M100 18 Q124 50 100 82" fill="none" ${S}/>`,
    catapult: c => `<line x1="20" y1="80" x2="120" y2="80" ${S}/><path d="M50 80 L70 52 L90 80" fill="none" ${S}/><line x1="34" y1="70" x2="112" y2="30" ${S}/><circle cx="112" cy="26" r="7" fill="${c}" ${S}/><path d="M128 20 Q138 30 140 44" fill="none" stroke="${c}" stroke-width="2" stroke-dasharray="3 4"/>`,
    glider: c => `<path d="M14 54 Q70 40 136 50 L136 56 Q70 52 14 60 Z" fill="${c}" ${S}/><path d="M70 50 L58 18 L84 18 L80 49" fill="${c}" ${S}/><path d="M118 50 L132 32 L138 34 L130 52" fill="${c}" ${S}/>`,
    car: c => `<path d="M16 66 L26 46 L58 40 L78 26 L110 26 L128 44 L136 66 Z" fill="${c}" ${S}/><circle cx="42" cy="68" r="11" fill="var(--sheet)" ${S}/><circle cx="112" cy="68" r="11" fill="var(--sheet)" ${S}/>`,
    windtunnel: c => `<path d="M10 24 L50 38 L100 38 L140 20 M10 84 L50 66 L100 66 L140 84" fill="none" ${S}/><path d="M60 54 Q75 44 92 52 Q75 56 60 54 Z" fill="${c}" ${S}/><path d="M18 46 L40 46 M18 54 L40 54 M18 62 L40 62" stroke="${c}" stroke-width="2.4"/>`,
    interferometer: c => `<rect x="62" y="44" width="16" height="16" transform="rotate(45 70 52)" fill="${c}" ${S}/><line x1="16" y1="52" x2="130" y2="52" stroke="${c}" stroke-width="2"/><line x1="70" y1="14" x2="70" y2="90" stroke="${c}" stroke-width="2"/><line x1="130" y1="38" x2="130" y2="66" ${S}/><line x1="56" y1="14" x2="84" y2="14" ${S}/><rect x="6" y="46" width="10" height="12" fill="var(--ink)"/>`,
    sensor: c => `<rect x="38" y="28" width="74" height="48" rx="4" fill="${c}" ${S}/><path d="M50 28 L50 16 M64 28 L64 16 M78 28 L78 16 M92 28 L92 16 M50 76 L50 88 M64 76 L64 88 M78 76 L78 88 M92 76 L92 88" ${S}/><rect x="58" y="40" width="34" height="24" rx="2" fill="var(--sheet)" ${S}/>`,
    cubesat: c => `<path d="M50 30 L90 22 L120 34 L80 42 Z" fill="${c}" ${S}/><path d="M50 30 L50 74 L80 86 L80 42 Z M80 86 L120 76 L120 34" fill="none" ${S}/><path d="M50 52 L18 46 M18 30 L18 62 M120 56 L142 60 M142 44 L142 76" ${S}/>`,
    airfoil: c => `<path d="M14 52 Q40 22 96 36 Q122 44 138 52 Q100 56 60 62 Q30 64 14 52 Z" fill="${c}" ${S}/><path d="M40 38 L40 20 M64 34 L64 16 M88 36 L88 18" stroke="var(--ink)" stroke-width="2" stroke-dasharray="2 3"/>`,
    spring: c => `<line x1="20" y1="16" x2="130" y2="16" ${S}/><path d="M60 16 L60 24 L48 30 L72 38 L48 46 L72 54 L48 62 L60 68 L60 76" fill="none" ${S}/><rect x="84" y="24" width="14" height="30" fill="${c}" ${S}/><line x1="91" y1="16" x2="91" y2="24" ${S}/><line x1="91" y1="54" x2="91" y2="76" ${S}/><rect x="40" y="76" width="70" height="14" rx="3" fill="${c}" ${S}/>`,
    gear: c => `<path d="M75 22 L82 22 L84 32 L94 36 L102 30 L108 36 L102 44 L106 54 L116 56 L116 64 L106 66 L102 76 L108 84 L102 90 L94 84 L84 88 L82 98 L68 98 L66 88 L56 84 L48 90 L42 84 L48 76 L44 66 L34 64 L34 56 L44 54 L48 44 L42 36 L48 30 L56 36 L66 32 L68 22 Z" fill="${c}" ${S} transform="translate(0 -12)"/><circle cx="75" cy="48" r="11" fill="var(--sheet)" ${S}/>`,
    train: c => `<rect x="26" y="34" width="98" height="26" rx="4" fill="${c}" ${S}/><circle cx="46" cy="70" r="10" fill="var(--sheet)" ${S}/><circle cx="104" cy="70" r="10" fill="var(--sheet)" ${S}/><line x1="10" y1="82" x2="140" y2="82" ${S}/>`,
    boat: c => `<path d="M14 50 L136 50 Q124 80 82 82 L46 82 Q22 76 14 50 Z" fill="${c}" ${S}/><path d="M8 88 Q30 82 52 88 T96 88 T140 88" fill="none" stroke="var(--ink)" stroke-width="2"/>`,
    panel: c => `<path d="M16 40 L96 26 L134 42 L54 56 Z" fill="${c}" ${S}/><path d="M16 40 L16 52 L54 68 L54 56 M54 68 L134 54 L134 42" fill="none" ${S}/><path d="M24 46 L32 49 L40 53 L48 56" stroke="var(--ink)" stroke-width="1.6"/><path d="M60 82 L60 72 M128 70 L128 60 M94 18 L94 8" ${S}/>`,
    beam: c => `<rect x="10" y="40" width="14" height="40" fill="var(--ink)"/><path d="M24 54 Q60 34 90 52 T136 50" fill="none" stroke="${c}" stroke-width="5"/><path d="M24 62 Q60 74 90 58 T136 60" fill="none" stroke="var(--ink)" stroke-width="2" stroke-dasharray="3 4"/>`,
    flag: c => `<line x1="30" y1="14" x2="30" y2="90" ${S}/><path d="M30 18 Q60 8 80 22 T130 24 L130 54 Q110 64 82 52 T30 50 Z" fill="${c}" ${S}/><path d="M8 70 L22 70 M8 80 L22 80" stroke="${c}" stroke-width="2.4"/>`,
    lattice: c => `<path d="M14 76 L136 76 M14 30 L136 30 M14 30 L14 76 M136 30 L136 76 M14 76 L44 30 L74 76 L104 30 L136 76 M44 76 L74 30 L104 76" fill="none" stroke="${c}" stroke-width="4"/><path d="M14 30 L136 30" ${S}/>`,
    weather: c => `<line x1="75" y1="40" x2="75" y2="92" ${S}/><path d="M75 40 L55 26 M75 40 L95 26 M75 40 L75 18" ${S}/><circle cx="55" cy="26" r="6" fill="${c}" ${S}/><circle cx="95" cy="26" r="6" fill="${c}" ${S}/><circle cx="75" cy="16" r="6" fill="${c}" ${S}/><rect x="62" y="56" width="26" height="18" fill="${c}" ${S}/>`,
    drop: c => `<rect x="60" y="14" width="30" height="22" rx="3" fill="${c}" ${S}/><path d="M75 44 L75 60 M68 54 L75 62 L82 54" fill="none" ${S}/><line x1="20" y1="84" x2="130" y2="84" ${S}/><path d="M54 84 L60 74 M96 84 L90 74 M75 84 L75 72" stroke="${c}" stroke-width="2.4"/>`,
    robot: c => `<rect x="50" y="16" width="50" height="56" rx="6" fill="${c}" ${S}/><circle cx="64" cy="34" r="5" fill="var(--sheet)" ${S}/><circle cx="86" cy="34" r="5" fill="var(--sheet)" ${S}/><circle cx="56" cy="80" r="11" fill="var(--sheet)" ${S}/><circle cx="94" cy="80" r="11" fill="var(--sheet)" ${S}/>`,
    loop: c => `<rect x="34" y="18" width="82" height="66" rx="10" fill="none" stroke="${c}" stroke-width="7"/><rect x="34" y="18" width="82" height="66" rx="10" fill="none" ${S}/><rect x="26" y="58" width="16" height="22" fill="var(--critical)" ${S}/><path d="M78 12 L90 18 L78 24" fill="none" ${S}/>`,
    wearable: c => `<rect x="56" y="26" width="38" height="48" rx="10" fill="${c}" ${S}/><path d="M62 26 L66 6 L84 6 L88 26 M62 74 L66 94 L84 94 L88 74" fill="none" ${S}/><path d="M64 50 L70 50 L74 40 L78 60 L82 50 L86 50" fill="none" stroke="var(--sheet)" stroke-width="2.4"/>`,
    knot: c => `<path d="M75 16 C120 16 120 70 75 70 C40 70 30 40 60 30 C95 18 120 60 92 82 C70 98 30 84 38 56 C44 34 90 30 100 52" fill="none" stroke="${c}" stroke-width="6" stroke-linecap="round"/>`,
    origami: c => `<path d="M30 76 L75 18 L120 76 Z" fill="${c}" ${S}/><path d="M75 18 L75 76 M52 47 L98 47 M30 76 L98 47 M120 76 L52 47" stroke="var(--sheet)" stroke-width="1.6"/>`,
  };

  // ---- Jigsaw geometry. Tab values are absolute (+1 = bump towards +x/+y), so neighbours tile.
  function edge(x0, y0, x1, y1, ox, oy) {
    if (!ox && !oy) return `L${x1},${y1}`;
    const P = a => [x0 + (x1 - x0) * a, y0 + (y1 - y0) * a];
    const [ax, ay] = P(0.36), [bx, by] = P(0.28), [cx, cy] = P(0.72), [dx, dy] = P(0.64);
    return `L${ax},${ay} C${bx + ox},${by + oy} ${cx + ox},${cy + oy} ${dx},${dy} L${x1},${y1}`;
  }
  function piecePath(x, y, w, h, t, rgt, b, l) {
    const k = Math.min(w, h) * 0.32;
    return `M${x},${y} ` + edge(x, y, x + w, y, 0, t * k) + " " + edge(x + w, y, x + w, y + h, rgt * k, 0) + " " +
      edge(x + w, y + h, x, y + h, 0, b * k) + " " + edge(x, y + h, x, y, l * k, 0) + " Z";
  }
  function grid(n) { const cols = Math.max(1, Math.round(Math.sqrt(n * 1.5))); return { cols, rows: Math.ceil(n / cols) }; }

  function grid(n) { const cols = Math.max(1, Math.round(Math.sqrt(n * 1.5))); return { cols, rows: Math.ceil(n / cols) }; }
  const pieceOf = (g, groups) => groups <= MAX_PIECES ? g : Math.floor(g * MAX_PIECES / groups);

  function illustration(course, color, groups) {
    const id = "il-" + course.id;
    const n = Math.min(groups, MAX_PIECES);
    const { cols, rows } = grid(n);
    const pw = 150 / cols, ph = 100 / rows;
    const r = SimCore.mulberry32(SimCore.hash(course.id));
    const hT = Array.from({ length: rows + 1 }, (_, i) => Array.from({ length: cols }, () => (i === 0 || i === rows) ? 0 : (r() < 0.5 ? -1 : 1)));
    const vT = Array.from({ length: rows }, () => Array.from({ length: cols + 1 }, (_, j) => (j === 0 || j === cols) ? 0 : (r() < 0.5 ? -1 : 1)));
    let defs = `<g id="${id}"><rect width="150" height="100" fill="color-mix(in srgb, ${color} 12%, var(--sheet))"/>${(M[course.motif] || M.sensor)(color)}</g>`;
    let body = `<use href="#${id}" class="ghost"/>`;
    for (let p = 0; p < n; p++) {
      const i = Math.floor(p / cols), j = p % cols;
      const d = piecePath(j * pw, i * ph, pw, ph, hT[i][j], vT[i][j + 1], hT[i + 1][j], vT[i][j]);
      defs += `<clipPath id="${id}-c${p}"><path d="${d}"/></clipPath>`;
      const dx = (r() - 0.5) * 40, dy = (r() - 0.5) * 30, rot = (r() - 0.5) * 60;
      body += `<g class="pz" data-p="${p}" style="--dx:${dx.toFixed(1)}px;--dy:${dy.toFixed(1)}px;--rot:${rot.toFixed(0)}deg;transform-origin:${(j * pw + pw / 2).toFixed(1)}px ${(i * ph + ph / 2).toFixed(1)}px">` +
        `<g clip-path="url(#${id}-c${p})"><use href="#${id}"/></g><path d="${d}" fill="none" stroke="var(--sheet)" stroke-width="1"/></g>`;
    }
    return `<svg class="illus" viewBox="-8 -8 166 116" role="img" aria-label="${course.activity}: illustration split into ${groups} project groups"><defs>${defs}</defs>${body}</svg>`;
  }

  // ---- State
  let D, rules, base, cohortSize = {}, over = {}, seed = 2027, mode = "course", period = "P1", revealed = new Set(), stepIdx = {}, sched, root;
  const courses = () => base.map(c => {
    const o = over[c.id] || {};
    const merged = { ...c, ...o };
    merged.n_students = o.students != null ? Math.round(o.students) : SimCore.participants(c, cohortSize);
    return merged;
  });

  function fmtDate(iso, addDays) {
    const d = new Date(iso + "T12:00:00Z"); d.setUTCDate(d.getUTCDate() + addDays);
    return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}`;
  }
  function isoWeek(iso, addDays) {
    const d = new Date(iso + "T12:00:00Z"); d.setUTCDate(d.getUTCDate() + addDays);
    const t = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()));
    t.setUTCDate(t.getUTCDate() + 4 - (t.getUTCDay() || 7));
    return Math.ceil(((t - Date.UTC(t.getUTCFullYear(), 0, 1)) / 864e5 + 1) / 7);
  }

  function courseOrder(p) {
    const cs = courses();
    return sched.periods[p].courses.slice().sort((a, b) => cs[a].weeks[0] - cs[b].weeks[0] || a - b);
  }
  function stepsOf(p) {
    const P = sched.periods[p];
    if (mode === "week") return Array.from({ length: rules.teaching_weeks }, (_, w) => P.events.filter(e => e.w === w));
    return courseOrder(p).map(ci => P.events.filter(e => e.c === ci));
  }
  function recompute(resetAll) {
    sched = SimCore.schedule(courses(), rules, seed);
    if (resetAll) for (const p of PERIODS) stepIdx[p] = Infinity;
    revealed = new Set();
    for (const p of PERIODS) {
      const steps = stepsOf(p);
      stepIdx[p] = Math.min(stepIdx[p] ?? Infinity, steps.length);
      steps.slice(0, stepIdx[p]).flat().forEach(e => revealed.add(p + ":" + e.id));
    }
  }
  function colorOf(ci) {
    const P = sched.periods[courses()[ci].period];
    return COLORS[P.courses.indexOf(ci) % COLORS.length];
  }
  const fmt = v => (Math.round(v * 10) / 10).toString();
  function state(res) {
    if (res.supShort === 0 && res.unsupShort === 0) return ["var(--good)", "fits"];
    return res.supGot >= 0.75 * res.supReq ? ["var(--warning)", "squeezed"] : ["var(--critical)", "does not fit"];
  }
  const takersText = c => (c.takers || []).map(t => `${t.cohort} (${t.students != null ? t.students + " st" : Math.round((t.share ?? 1) * 100) + "%"})`).join(", ");

  // ---- Rendering
  function render() {
    const cs = courses();
    const cal = D.calendar.periods[period];
    const P = sched.periods[period];
    const steps = stepsOf(period);
    const done = stepIdx[period] >= steps.length;
    const label = mode === "week" ? "week" : "course";
    root.querySelector("#sim-periods").innerHTML = PERIODS.map(p => {
      const c = D.calendar.periods[p];
      return `<button type="button" data-p="${p}" aria-pressed="${p === period}"><b>${p}</b> <span>${c.term} · v${isoWeek(c.start, 0)}–${isoWeek(c.start, 7 * rules.teaching_weeks - 3)} · ${sched.periods[p].courses.length} courses</span></button>`;
    }).join("");
    root.querySelector("#sim-progress").textContent = done ? `All ${steps.length} ${label}s placed` : `${stepIdx[period]} of ${steps.length} ${label}s placed`;
    root.querySelector("#sim-step").disabled = done;

    const curC = mode === "course" && stepIdx[period] > 0 && !done ? courseOrder(period)[stepIdx[period] - 1] : null;
    root.querySelector("#sim-cards").innerHTML = P.courses.map(ci => {
      const c = cs[ci], res = sched.results[ci], color = colorOf(ci);
      const anyPlaced = P.events.some(e => e.c === ci && revealed.has(period + ":" + e.id));
      const st = state(res);
      const w = c.weeks;
      const mods = c.modules && c.modules.length ? c.modules.join(", ") : "no LAB/PRO module";
      return `<article class="sc${anyPlaced ? " broken" : ""}${ci === curC ? " current" : ""}" data-c="${ci}" style="--cc:${color}">
        <div class="sc-head"><span class="sc-dot"></span><span class="sc-mock">Mock example</span><span class="sc-coh">${takersText(c)}</span></div>
        <div class="sc-course">${c.code} ${c.course_name || ""}</div>
        <h3>${c.activity}</h3>
        ${illustration(c, color, res.groups)}
        <p>${c.text}</p>
        <div class="sc-meta">Syllabus: ${mods}${c.pick_note ? ` · ${c.pick_note}` : ""}</div>
        <div class="sc-meta">${res.students} students · ${res.groups} groups · ${res.sections} section${res.sections === 1 ? "" : "s"} · weeks ${w[0]}–${w[1]} · owns ${res.own.map(i => SimCore.HALF[i]).join(", ")}</div>
        <div class="sc-in">
          <label>Students<input type="number" min="1" max="500" step="1" value="${res.students}" data-f="students" id="sim-${c.id}-students"></label>
          <label>Group size<input type="number" min="1" max="10" step="1" value="${c.group_size}" data-f="group_size" id="sim-${c.id}-gs"></label>
          <label>Supervised h<input type="number" min="0" max="40" step="4" value="${c.sup_h}" data-f="sup_h" id="sim-${c.id}-sup"></label>
          <label>Unsuperv. h<input type="number" min="0" max="40" step="2" value="${c.unsup_h}" data-f="unsup_h" id="sim-${c.id}-unsup"></label>
          <label>Period<select data-f="period" id="sim-${c.id}-period">${PERIODS.map(p => `<option${p === c.period ? " selected" : ""}>${p}</option>`).join("")}</select></label>
        </div>
        <div class="sc-res"><span class="pill"><i style="background:${st[0]}"></i>${st[1]}</span> supervised ${fmt(res.supGot)} of ${res.supReq} h · unsupervised ${fmt(res.unsupGot)} of ${res.unsupReq} h</div>
      </article>`;
    }).join("");

    // Board
    const W = rules.teaching_weeks;
    let h = `<div class="bd-corner"></div>`;
    for (let w = 0; w < W; w++) h += `<div class="bd-wk"><b>v${isoWeek(cal.start, 7 * w)}</b><span>${fmtDate(cal.start, 7 * w)}</span></div>`;
    SimCore.HALF.forEach((lab, s) => {
      h += `<div class="bd-row">${lab.replace(" am", " 08–12").replace(" pm", " 13–17")}</div>`;
      for (let w = 0; w < W; w++) h += cellHtml(P.day[w][s], "day");
    });
    h += `<div class="bd-sep" style="grid-column: 1 / span ${W + 1}">Open access, unsupervised (staffed evenings and Saturday; up to ${rules.visit_members} members per group visit)</div>`;
    SimCore.OPEN_ROWS.forEach((lab, j) => {
      h += `<div class="bd-row">${lab}</div>`;
      for (let w = 0; w < W; w++) h += cellHtml(P.open[w][j], "open");
    });
    const board = root.querySelector("#sim-board");
    board.style.setProperty("--weeks", W);
    board.innerHTML = h;
    root.querySelector("#sim-util").innerHTML =
      `Each block is one project group, its width the number of people (a cell row is ${UNITS} people); dark units are teachers. ` +
      `Daytime place use ${Math.round(P.dayUtil * 100)}% · open-access use ${Math.round(P.openUtil * 100)}% · seed ${seed}`;
    renderSummary();
  }

  function cellHtml(cell, kind) {
    if (kind === "day" && cell.blocked) return `<div class="bd-cell blocked" title="Booked for other use (maintenance, outreach, inductions)"></div>`;
    if (kind === "open" && !cell.open) return `<div class="bd-cell closed" title="Not staffed"></div>`;
    const cs = courses();
    const rows = Math.ceil(cell.cap / UNITS);
    let inner = "";
    for (const e of cell.events) {
      const vis = revealed.has(period + ":" + e.id);
      const c = cs[e.c], color = colorOf(e.c), sizes = sched.results[e.c].sizes;
      const what = e.kind === "sup" ? "supervised session" : "open-access visit";
      for (const g of e.groups) {
        const n = e.kind === "sup" ? sizes[g] : e.persons;
        inner += `<span class="pc${vis ? "" : " hidden-pc"}" data-c="${e.c}" data-e="${e.id}" data-g="${g}" style="grid-column: span ${Math.min(n, UNITS)};background:${color}" title="${c.code} ${c.activity} · group ${g + 1} (${n} ${n === 1 ? "person" : "people"}) · ${what}"></span>`;
      }
      if (e.kind === "sup") for (let t = 0; t < e.teachers; t++)
        inner += `<span class="pc teacher${vis ? "" : " hidden-pc"}" data-c="${e.c}" title="Teacher, ${c.code}"></span>`;
    }
    return `<div class="bd-cell ${kind}" style="--rows:${rows}" title="${cell.used} of ${cell.cap} people">${inner}</div>`;
  }

  function renderSummary() {
    const cs = courses();
    const rows = cs.map((c, i) => ({ c, r: sched.results[i] })).sort((a, b) => a.c.period.localeCompare(b.c.period) || a.c.id.localeCompare(b.c.id));
    const pu = PERIODS.map(p => `${p}: daytime ${Math.round(sched.periods[p].dayUtil * 100)}%, open ${Math.round(sched.periods[p].openUtil * 100)}%`).join(" · ");
    root.querySelector("#sim-summary").innerHTML = `<p class="sim-pu">Every activity below is a <b>mock example</b> attached to a real course. Place use per period: ${pu}</p><div class="tablewrap"><table><thead><tr><th>Period</th><th>Course</th><th>Mock activity</th><th>Taken by</th><th class="num">Students</th><th class="num">Group size</th><th class="num">Supervised h</th><th class="num">Unsupervised h</th><th>State</th></tr></thead><tbody>` +
      rows.map(({ c, r }) => {
        const st = state(r);
        return `<tr><td>${c.period}</td><td>${c.code} ${c.course_name || ""}</td><td>${c.activity}</td><td>${takersText(c)}</td><td class="num">${r.students}</td><td class="num">${c.group_size}</td><td class="num">${fmt(r.supGot)} / ${r.supReq}</td><td class="num">${fmt(r.unsupGot)} / ${r.unsupReq}</td><td><span class="pill"><i style="background:${st[0]}"></i>${st[1]}</span></td></tr>`;
      }).join("") + `</tbody></table></div>`;
  }

  // ---- Stepping with animation
  function step() {
    const steps = stepsOf(period);
    if (stepIdx[period] >= steps.length) return;
    const evs = steps[stepIdx[period]];
    stepIdx[period]++;
    evs.forEach(e => revealed.add(period + ":" + e.id));
    const newIds = new Set(evs.map(e => e.id));
    render();
    animate(newIds);
  }

  function animate(newIds) {
    const pcs = [...root.querySelectorAll("#sim-board .pc[data-e]")].filter(s => newIds.has(+s.dataset.e));
    if (reduceMotion() || !pcs.length) return;
    pcs.forEach(s => s.classList.add("incoming"));
    const total = Math.min(900, pcs.length * 6);
    pcs.forEach((s, i) => {
      const ci = +s.dataset.c;
      const card = root.querySelector(`#sim-cards .sc[data-c="${ci}"]`);
      const src = card && card.querySelector(`.illus .pz[data-p="${pieceOf(+s.dataset.g, sched.results[ci].groups)}"]`);
      const a = (src || card || s).getBoundingClientRect(), b = s.getBoundingClientRect();
      if (!a.width || !b.width) { s.classList.remove("incoming"); return; }
      const fly = document.createElement("div");
      fly.className = "sim-fly";
      fly.style.background = colorOf(ci);
      fly.style.left = a.left + a.width / 2 - 5 + "px";
      fly.style.top = a.top + a.height / 2 - 5 + "px";
      document.body.appendChild(fly);
      const dx = b.left + b.width / 2 - (a.left + a.width / 2), dy = b.top + b.height / 2 - (a.top + a.height / 2);
      const delay = Math.round(i / pcs.length * total);
      requestAnimationFrame(() => requestAnimationFrame(() => {
        fly.style.transitionDelay = delay + "ms";
        fly.style.transform = `translate(${dx}px, ${dy}px) scale(0.7)`;
      }));
      const finish = () => { fly.remove(); s.classList.remove("incoming"); };
      fly.addEventListener("transitionend", finish, { once: true });
      setTimeout(finish, delay + 1200);
    });
  }

  function init(el, data) {
    root = el; D = data; rules = { ...data.rules, teaching_weeks: data.calendar.teaching_weeks };
    base = data.courses;
    cohortSize = data.cohort_sizes;
    root.querySelector("#sim-seed").value = seed;
    root.querySelector("#sim-open").value = rules.open_slots_per_week;
    recompute(true);
    render();
    root.addEventListener("click", e => {
      const pb = e.target.closest("#sim-periods button");
      if (pb) { period = pb.dataset.p; render(); return; }
      const mb = e.target.closest("#sim-mode button");
      if (mb) {
        mode = mb.dataset.m;
        root.querySelectorAll("#sim-mode button").forEach(b => b.setAttribute("aria-pressed", String(b === mb)));
        recompute(true); render(); return;
      }
      if (e.target.closest("#sim-step")) { step(); return; }
      if (e.target.closest("#sim-reset")) { stepIdx[period] = 0; recompute(false); render(); return; }
      if (e.target.closest("#sim-all")) { stepIdx[period] = Infinity; recompute(false); render(); return; }
      if (e.target.closest("#sim-shuffle")) {
        seed = Math.floor(Math.random() * 1e6); root.querySelector("#sim-seed").value = seed; recompute(false); render(); return;
      }
    });
    root.addEventListener("change", e => {
      const t = e.target;
      if (t.id === "sim-seed") { seed = Math.max(0, Math.floor(+t.value || 0)); recompute(false); render(); return; }
      if (t.id === "sim-open") { rules.open_slots_per_week = Math.max(0, Math.min(6, +t.value || 0)); recompute(false); render(); return; }
      const card = t.closest(".sc");
      if (card && t.dataset.f) {
        const c = courses()[+card.dataset.c];
        let v = t.dataset.f === "period" ? t.value : Math.max(0, +t.value || 0);
        if (t.dataset.f === "group_size") v = Math.max(1, Math.min(10, Math.round(v)));
        over[c.id] = { ...(over[c.id] || {}), [t.dataset.f]: v };
        recompute(false); render();
      }
    });
    // Hovering a course card or one of its pieces highlights that course's pieces
    let focus = "";
    const setFocus = ci => {
      if (ci === focus) return;
      focus = ci;
      root.querySelectorAll("#sim-board .pc[data-c]").forEach(pc => pc.classList.toggle("dim", ci !== "" && pc.dataset.c !== ci));
    };
    root.addEventListener("pointerover", e => { const c = e.target.closest(".sc, .pc[data-c]"); setFocus(c ? c.dataset.c : ""); });
    root.addEventListener("pointerleave", () => setFocus(""));
  }

  return { init };
})();

if (typeof module !== "undefined") module.exports = { SimCore };
