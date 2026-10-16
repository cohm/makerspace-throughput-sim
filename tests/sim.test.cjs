// Rules check for the course simulation scheduler (web/sim.js SimCore).
// Usage: node tests/sim.test.cjs   (after run.py has written web/defaults.json)
const assert = require("assert");
const path = require("path");
const { SimCore } = require("../web/sim.js");
const D = require(path.join(__dirname, "../web/defaults.json")).sim;
const rules = { ...D.rules, teaching_weeks: D.calendar.teaching_weeks };
const withN = cs => cs.map(c => ({ ...c, n_students: SimCore.participants(c, D.cohort_sizes) }));
const COURSES = withN(D.courses);

function check(seed, courses = COURSES) {
  const s = SimCore.schedule(courses, rules, seed);
  for (const [p, P] of Object.entries(s.periods)) {
    P.day.flat().forEach(c => {
      assert(c.used <= c.cap, `${p}: daytime over capacity`);
      if (c.blocked) assert.strictEqual(c.events.length, 0, `${p}: event in blocked slot`);
      const persons = c.events.reduce((t, e) => t + e.groups.reduce((u, g) => u + s.results[e.c].sizes[g], 0) + e.teachers, 0);
      assert.strictEqual(persons, c.used, `${p}: used persons do not match placed groups`);
    });
    P.open.flat().forEach(c => {
      assert(c.used <= c.cap, `${p}: open slot over capacity`);
      if (!c.open) assert.strictEqual(c.events.length, 0, `${p}: event in unstaffed slot`);
    });
    const perSecWeek = {};
    for (const e of P.events) {
      const c = courses[e.c];
      assert.strictEqual(c.period, p, "event outside the course's period");
      assert(e.w >= c.weeks[0] - 1 && e.w <= c.weeks[1] - 1, `${c.id}: event outside project weeks`);
      if (e.kind === "sup") {
        assert(s.results[e.c].own.includes(e.slot), `${c.id}: session outside owned half-days`);
        const k = `${e.c}:${e.groups[0]}:${e.w}`;
        perSecWeek[k] = (perSecWeek[k] || 0) + 1;
        assert(perSecWeek[k] <= rules.max_sessions_per_week, `${c.id}: too many sessions in a week`);
      } else assert(e.persons <= rules.visit_members, "visit with too many members");
    }
  }
  s.results.forEach((r, i) => {
    assert(r.supGot <= r.supReq + 1e-9 && r.unsupGot <= r.unsupReq + 1e-9, `${r.id}: delivered more than requested`);
    assert.strictEqual(r.sizes.reduce((a, b) => a + b, 0), courses[i].n_students, `${r.id}: group sizes do not add up`);
    assert(r.sizes.every(x => x >= 1 && x <= (courses[i].group_size || 4)), `${r.id}: group size out of range`);
  });
  return s;
}

const a = check(2027), b = check(2027), c = check(7);
assert.deepStrictEqual(a.results, b.results, "same seed must give the same schedule");
const where = s => Object.values(s.periods).flatMap(P => P.events.map(e => `${e.c}:${e.kind}:${e.w}:${e.slot}`));
assert.notDeepStrictEqual(where(a), where(c), "different seeds should place pieces differently");
for (let sd = 1; sd <= 30; sd++) check(sd);
// Group sizes 1 and 10 are handled
check(5, COURSES.map(x => ({ ...x, group_size: 1 })));
check(5, COURSES.map(x => ({ ...x, group_size: 10 })));
// Participants are summed over takers; explicit students override
const shared = { id: "X", takers: [{ cohort: "CFATE-3", share: 1 }, { cohort: "TTEMM-1", share: 0.5 }] };
assert.strictEqual(SimCore.participants(shared, D.cohort_sizes), Math.round(D.cohort_sizes["CFATE-3"] + 0.5 * D.cohort_sizes["TTEMM-1"]));
assert.strictEqual(SimCore.participants({ ...shared, students: 77 }, D.cohort_sizes), 77);
assert.strictEqual(SimCore.participants({ takers: [{ cohort: "CTFYS-1", students: 30 }] }, D.cohort_sizes), 30);
// SK1105 keeps its real Tue/Wed slots
const sk = a.results[D.courses.findIndex(x => x.code === "SK1105")];
assert.deepStrictEqual(sk.own.map(i => SimCore.HALF[i]), ["Tue am", "Tue pm", "Wed am", "Wed pm"]);

const short = a.results.filter(r => r.supShort || r.unsupShort).map(r => `${r.id} sup ${r.supGot.toFixed(1)}/${r.supReq} unsup ${r.unsupGot.toFixed(1)}/${r.unsupReq}`);
console.log("ok: rules hold for 34 schedules; seed 2027 short:", short.length ? short.join("; ") : "none");
console.log("place use:", Object.entries(a.periods).map(([p, P]) => `${p} day ${Math.round(P.dayUtil * 100)}% open ${Math.round(P.openUtil * 100)}%`).join(", "));
