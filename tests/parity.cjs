// Parity check: JS model (web/model.js) vs Python results (run `run.py` first).
// Usage: node tests/parity.cjs
const fs = require("fs");
const path = require("path");
const M = require("../web/model.js");
const root = path.join(__dirname, "..");
const D = JSON.parse(fs.readFileSync(path.join(root, "web/defaults.json"), "utf8"));
const csv = fs.readFileSync(path.join(root, "results/scenarios.csv"), "utf8").trim().split("\n");
const head = csv[0].split(",");
const py = Object.fromEntries(csv.slice(1).map(l => {
  const v = l.match(/("[^"]*"|[^,]+|(?<=,)(?=,))/g);   // naive CSV (description is quoted)
  return [v[0], Object.fromEntries(head.map((h, i) => [h, v[i]]))];
}));
const params = name => {
  const sc = { ...D.scenarios[name] }; delete sc.label; delete sc.description;
  return M.deepMerge(D.base, sc);
};
let fail = 0;
const check = (label, js, ref, tol) => {
  const ok = Math.abs(js - ref) <= tol;
  if (!ok) fail++;
  console.log(`${ok ? "ok  " : "FAIL"} ${label}: js=${js.toFixed(3)} py=${Number(ref).toFixed(3)} (tol ${tol})`);
};
const p0 = params("optimal");
const a = M.analytic(D.cohorts, D.facility, p0);
check("fluid Y_max", a.Y_max, D.check.analytic.Y_max, 1e-9);
check("total sections", a.total_sections, D.check.analytic.total_sections, 0);
check("open access Z", M.openAccess(D.cohorts, D.facility, p0).Z, D.check.Z, 1e-9);
const planned = Number(head.find(h => h.startsWith("mean_delivered_if_plan_")).match(/(\d+)h$/)[1]);
for (const name of Object.keys(D.scenarios)) {
  const p = params(name);
  // "Guaranteed Y" is a threshold on a noisy share, so compare the share itself.
  const m = Number(py[name].Y_sim) / p.course.session_hours;
  const sh = M.simulate(D.cohorts, D.facility, p, m, 200, 7).shareServed;
  check(`${name}: share served at Y=${py[name].Y_sim} h`, sh, py[name].share_served_at_Y, 0.02);
  const r = M.simulate(D.cohorts, D.facility, p, Math.ceil(planned / p.course.session_hours), 100, 11);
  check(`${name}: mean h if plan ${planned}`, r.meanHours, py[name][`mean_delivered_if_plan_${planned}h`], 0.5);
}
process.exit(fail ? 1 : 0);
