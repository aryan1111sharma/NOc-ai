// Restore LibreChat agents from a JSON file (EJSON array from export-agents) that the wrapper
// copies into the mongo container at AGENT_FILE. Resolves each agent's owner by email —
// falling back to the first ADMIN user when the emailed owner is absent — and upserts by the
// string `id`. Idempotent: re-running updates in place. See chat/RUNBOOK.md.
const path = process.env.AGENT_FILE || "/tmp/agents.restore.json";
let raw;
try {
  raw = require("fs").readFileSync(path, "utf8").replace(/^﻿/, "");
} catch (e) {
  print("ERROR: cannot read " + path + " (" + e.message + ")");
  quit(1);
}
const parsed = EJSON.parse(raw);
const list = Array.isArray(parsed) ? parsed : [parsed];
let n = 0;
list.forEach(function (a) {
  const email = a.__author_email;
  delete a.__author_email;
  let author = null;
  if (email) {
    const u = db.users.findOne({ email: email });
    if (u) author = u._id;
  }
  if (!author) {
    const admin = db.users.findOne({ role: "ADMIN" });
    if (admin) author = admin._id;
  }
  if (author) a.author = author;
  a.updatedAt = new Date();
  db.agents.updateOne({ id: a.id }, { $set: a }, { upsert: true });
  n++;
  print(
    "restored " + a.id + " (" + a.name + ") owner=" +
    (author ? author + (email ? " <" + email + ">" : "") : "UNRESOLVED — no matching user or ADMIN")
  );
});
print("done: " + n + " agent(s)");
