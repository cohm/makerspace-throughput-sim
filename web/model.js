// JS port of model/capacity.py, model/schedule.py and model/open_access.py.
// Same parameter structure as data/scenarios.yaml (`base` merged with a scenario).
// Kept algorithmically identical to the Python; random streams differ, so simulated
// results agree statistically, not bit-for-bit (checked by tests/parity.mjs).

function mulberry32(seed) {
  let a = seed >>> 0;
  return function () {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function deepMerge(base, over) {
  const out = JSON.parse(JSON.stringify(base));
  for (const [k, v] of Object.entries(over || {})) {
    if (v && typeof v === "object" && !Array.isArray(v) && out[k] && typeof out[k] === "object") {
      out[k] = deepMerge(out[k], v);
    } else out[k] = JSON.parse(JSON.stringify(v));
  }
  return out;
}

function withSections(cohorts, p) {
  const S = p.course.section_size, g = p.course.group_size;
  return cohorts.map(c => ({ ...c, sections: Math.max(1, Math.ceil(c.students / S)), groups: Math.ceil(c.students / g) }));
}

function sessionPersonCap(fac, p) {
  let cap = fac.rooms.r3203.max_persons;
  if (p.room.side_rooms_in_sessions) cap += fac.rooms.r3202.max_persons + fac.rooms.studio3d.max_persons;
  return cap;
}

function parallelSections(fac, p) {
  return Math.floor(sessionPersonCap(fac, p) / (p.course.section_size + p.course.teachers_per_section));
}

function roomHoursPerPeriod(fac, p) {
  return p.calendar.teaching_weeks_per_period * 5 * fac.hours.daytime_bookable_h_per_day *
    p.room.booking_efficiency * (1 - p.room.other_daytime_use);
}

function analytic(cohorts, fac, p) {
  const df = withSections(cohorts, p);
  const k = parallelSections(fac, p);
  const X = p.course.courses_per_cohort;
  const roomYear = p.calendar.periods * roomHoursPerPeriod(fac, p);
  const supply = roomYear * k;
  const totalSections = df.reduce((s, c) => s + c.sections, 0);
  const yRoom = k > 0 ? supply / (X * totalSections) : 0;
  const yCap = p.timetable.project_weeks * p.course.max_sessions_per_week * p.course.session_hours;
  return {
    parallel_sections: k, room_hours_per_year: roomYear, section_hours_supply: supply,
    total_sections: totalSections, total_groups: df.reduce((s, c) => s + c.groups, 0),
    total_students: df.reduce((s, c) => s + c.students, 0),
    Y_room_bound: yRoom, Y_section_cap: yCap, Y_max: Math.min(yRoom, yCap),
  };
}

function assignPeriods(sections, p, rnd) {
  const P = p.calendar.periods;
  const mixRaw = p.timetable.period_mix, tot = mixRaw.reduce((a, b) => a + b, 0);
  const mix = mixRaw.map(x => x / tot);
  const out = new Array(sections.length);
  if (p.timetable.period_assignment === "balanced") {
    const load = new Array(P).fill(0);
    const idx = sections.map((_, i) => i).sort((a, b) => sections[b] - sections[a] || a - b);
    for (const i of idx) {
      let best = 0, bv = Infinity;
      for (let j = 0; j < P; j++) {
        const v = mix[j] > 0 ? (load[j] + sections[i]) / mix[j] : Infinity;
        if (v < bv) { bv = v; best = j; }
      }
      out[i] = best; load[best] += sections[i];
    }
    return out;
  }
  for (let i = 0; i < sections.length; i++) {
    let r = rnd(), j = 0;
    while (j < P - 1 && r >= mix[j]) { r -= mix[j]; j++; }
    out[i] = j;
  }
  return out;
}

function shuffle(arr, rnd) {
  for (let i = arr.length - 1; i > 0; i--) {
    const j = Math.floor(rnd() * (i + 1));
    [arr[i], arr[j]] = [arr[j], arr[i]];
  }
  return arr;
}

// One random timetable. Returns {ok[], delivered[]} per course instance.
function levelParams(levels, p) {
  const t = p.timetable, by = t.by_level || {};
  return levels.map(lv => [
    (by[lv] && by[lv].busy_fraction != null) ? by[lv].busy_fraction : t.busy_fraction,
    (by[lv] && by[lv].min_free_blocks != null) ? by[lv].min_free_blocks : t.min_free_blocks]);
}

function simulateOnce(sections, fac, p, m, rnd, levels) {
  const c = p.course, t = p.timetable, room = p.room;
  const L = c.session_hours;
  const W = 5 * Math.floor(fac.hours.daytime_bookable_h_per_day / L);
  const T = p.calendar.teaching_weeks_per_period;
  const w0 = T - t.project_weeks;
  const k = parallelSections(fac, p);
  const pAvail = room.booking_efficiency * (1 - room.other_daytime_use);
  const rho = t.busy_correlation;
  const lp = levelParams(levels || sections.map(() => null), p);
  const period = assignPeriods(sections, p, rnd);
  const ok = new Array(sections.length).fill(false);
  const delivered = new Array(sections.length).fill(0);

  for (let per = 0; per < p.calendar.periods; per++) {
    const idx = [];
    for (let i = 0; i < sections.length; i++) if (period[i] === per) idx.push(i);
    if (!idx.length) continue;
    // Busy if a uniform draw < the cohort's busy fraction; with probability rho the draw
    // is shared by all cohorts in the period (correlated timetables).
    const base = Array.from({ length: W }, () => rnd());
    const free = idx.map(ci => {
      const own = Array.from({ length: W }, () => rnd());
      const sh = Array.from({ length: W }, () => rnd() < rho);
      return own.map((o, j) => !((sh[j] ? base[j] : o) < lp[ci][0]));
    });
    for (const [r, row] of free.entries()) {
      let short = lp[idx[r]][1] - row.filter(Boolean).length;
      if (short > 0) {
        const busy = shuffle(row.map((v, j) => (v ? -1 : j)).filter(j => j >= 0), rnd);
        for (let q = 0; q < short && q < busy.length; q++) row[busy[q]] = true;
      }
    }
    const cap = Array.from({ length: T }, () => Array.from({ length: W }, () => (rnd() < pAvail ? k : 0)));
    const secCoh = [];
    idx.forEach((ci, r) => { for (let s = 0; s < sections[ci]; s++) secCoh.push(r); });
    const need = secCoh.map(() => m);

    for (let w = w0; w < T; w++) {
      const used = secCoh.map(() => new Array(W).fill(false));
      for (let round = 0; round < c.max_sessions_per_week; round++) {
        const slack = secCoh.map((r, s) => {
          let u = 0;
          for (let j = 0; j < W; j++) if (free[r][j] && cap[w][j] > 0 && !used[s][j]) u++;
          return u / Math.max(need[s], 1);
        });
        const order = shuffle(secCoh.map((_, s) => s), rnd);
        const pos = new Map(order.map((s, i) => [s, i]));
        order.sort((a, b) => slack[a] - slack[b] || pos.get(a) - pos.get(b));
        for (const s of order) {
          if (need[s] === 0) continue;
          const fr = free[secCoh[s]];
          let best = -1, bv = Infinity;
          for (let j = 0; j < W; j++) {
            if (!(fr[j] && cap[w][j] > 0 && !used[s][j])) continue;
            let pressure = 0;
            for (let q = 0; q < secCoh.length; q++) if (need[q] > 0 && free[secCoh[q]][j] && !used[q][j]) pressure++;
            const v = pressure / Math.max(cap[w][j], 1) + rnd() * 1e-3;
            if (v < bv) { bv = v; best = j; }
          }
          if (best < 0) continue;
          cap[w][best]--; need[s]--; used[s][best] = true;
        }
      }
    }
    idx.forEach((ci, r) => {
      let done = true, got = 0;
      secCoh.forEach((rr, s) => { if (rr === r) { if (need[s] > 0) done = false; got += m - need[s]; } });
      ok[ci] = done; delivered[ci] = got / sections[ci];
    });
  }
  return { ok, delivered };
}

// Run `draws` timetables for requested sessions m. Per cohort: share served, mean hours.
function simulate(cohorts, fac, p, m, draws, seed) {
  const df = withSections(cohorts, p);
  const X = p.course.courses_per_cohort;
  const sections = [], levels = [];
  df.forEach(c => { for (let x = 0; x < X; x++) { sections.push(c.sections); levels.push(c.level); } });
  const rnd = mulberry32(seed);
  const served = new Array(df.length).fill(0), hours = new Array(df.length).fill(0);
  const all = [];
  for (let d = 0; d < draws; d++) {
    const r = simulateOnce(sections, fac, p, m, rnd, levels);
    for (let i = 0; i < df.length; i++) {
      let okAll = true, h = 0;
      for (let x = 0; x < X; x++) { okAll = okAll && r.ok[i * X + x]; h += r.delivered[i * X + x]; }
      const hh = (h / X) * p.course.session_hours;
      served[i] += okAll ? 1 : 0; hours[i] += hh; all.push(hh);
    }
  }
  const W = df.reduce((s, c) => s + c.students, 0);
  all.sort((a, b) => a - b);
  return {
    perCohortServed: served.map(v => v / draws),
    perCohortHours: hours.map(v => v / draws),
    shareServed: served.reduce((a, b) => a + b, 0) / (draws * df.length),
    meanHours: df.reduce((s, c, i) => s + c.students * hours[i] / draws, 0) / W,
    p5Hours: all[Math.floor(0.05 * (all.length - 1))],
  };
}

function feasibleHours(cohorts, fac, p, draws, seed) {
  const L = p.course.session_hours;
  const mCap = p.timetable.project_weeks * p.course.max_sessions_per_week;
  const curve = [];
  let best = 0;
  for (let m = 1; m <= mCap; m++) {
    const r = simulate(cohorts, fac, p, m, draws, seed);
    curve.push([m * L, r.shareServed]);
    if (r.shareServed < p.sim.success_threshold) break;
    best = m;
  }
  return { Y: best * L, curve };
}

function staffedOpenHours(fac, p) {
  const st = fac.staffing;
  const hv = fac.hours.open_access_variants[p.open_access.hours_variant];
  const wanted = 5 * hv.evening_h_per_weekday + hv.weekend_h_per_week;
  const staffH = st.staff_fte * st.fte_hours_per_week * st.availability * st.staff_supervision_share;
  const saH = st.student_assistant_fte * st.fte_hours_per_week * st.availability;
  const byStaff = st.staff_per_open_hour ? staffH / st.staff_per_open_hour : Infinity;
  const bySa = st.sa_per_open_hour ? saH / st.sa_per_open_hour : Infinity;
  const hours = Math.min(wanted, byStaff, bySa);
  const binding = hours === wanted ? "opening hours" : byStaff <= bySa ? "staff" : "student assistants";
  return { wanted, byStaff, bySa, hours, binding };
}

function openAccess(cohorts, fac, p) {
  const df = withSections(cohorts, p);
  const oa = p.open_access, st = fac.staffing;
  const groups = df.reduce((s, c) => s + c.groups, 0) * p.course.courses_per_cohort;
  const hrs = staffedOpenHours(fac, p);
  const roomCap = oa.rooms.reduce((s, r) => s + fac.rooms[r].max_persons, 0);
  const sup = st.staff_per_open_hour + st.sa_per_open_hour;
  const simultaneous = Math.min(roomCap, st.max_users_per_supervisor * sup);
  const personH = hrs.hours * p.calendar.open_weeks_per_year * simultaneous * oa.occupancy * (1 - oa.noncourse_share);
  return { ...hrs, simultaneous, personH, groups, Z: personH / (groups * oa.persons_present_per_group) };
}

function machines(cohorts, fac, p) {
  const df = withSections(cohorts, p);
  const m = p.machines, wk = p.calendar.open_weeks_per_year;
  const groups = df.reduce((s, c) => s + c.groups, 0) * p.course.courses_per_cohort;
  return [
    ["3D printers", fac.machines.fdm_printers.count, m.printer_hours_per_week, m.printer_uptime, 1, m.printer_h_per_group],
    ["CNC", fac.machines.cnc.count, m.cnc_hours_per_week, m.cnc_uptime, m.cnc_share_of_groups, m.cnc_h_per_group],
    ["Metal machines", fac.machines.metal_machines.count, m.metal_hours_per_week, m.metal_uptime, m.metal_share_of_groups, m.metal_h_per_group],
  ].map(([name, n, hpw, up, share, hpg]) => {
    const supply = n * hpw * up * wk, demand = groups * share * hpg;
    return { resource: name, supply, demand, utilisation: demand / supply, maxPerGroup: supply / (groups * share) };
  });
}

const MakerModel = { mulberry32, deepMerge, withSections, parallelSections, roomHoursPerPeriod, analytic,
  simulate, feasibleHours, staffedOpenHours, openAccess, machines };
if (typeof module !== "undefined") module.exports = MakerModel;
