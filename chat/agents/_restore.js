// Restore LibreChat agents from a JSON file (EJSON array from export-agents) that the wrapper
// copies into the mongo container at AGENT_FILE. Resolves each agent's owner by email —
// falling back to the first ADMIN user when the emailed owner is absent — and upserts by the
// string `id`. Idempotent: re-running updates in place. See chat/RUNBOOK.md.
//
// Also restores permissions from __acl. LibreChat v0.8.x gates agent visibility on the
// `aclentries` collection keyed by the agent's Mongo _id, not on the agent document itself.
// Upserting into db.agents alone therefore produces an agent that NOBODY can select — not
// even its author — which is silent: the agent exists, is correctly configured, and simply
// never appears in the picker. When an entry carries no __acl (older agents.json), a minimum
// owner grant is synthesised for the resolved author so the agent is at least usable.
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

// Grant `spec` on `resourceId` unless an equivalent grant already exists. Returns a status
// string for the log so a restore reports exactly which permissions it created.
function grant(resourceId, spec, grantedBy) {
  let principalId = spec.principal;
  let principalModel = spec.principalType === "user" ? "User" : "Role";
  if (spec.principalType === "user") {
    const u = db.users.findOne({ email: spec.principal });
    if (!u) return "skipped (no user " + spec.principal + ")";
    principalId = u._id;
  }
  const filter = {
    resourceId: resourceId,
    resourceType: spec.resourceType,
    principalType: spec.principalType,
    principalId: principalId,
  };
  if (db.aclentries.findOne(filter)) return "exists";

  let roleId = null;
  if (spec.accessRoleId) {
    const ar = db.accessroles.findOne({ accessRoleId: spec.accessRoleId });
    if (ar) roleId = ar._id;
  }
  const now = new Date();
  db.aclentries.insertOne({
    resourceId: resourceId,
    resourceType: spec.resourceType,
    principalType: spec.principalType,
    principalId: principalId,
    principalModel: principalModel,
    permBits: spec.permBits,
    roleId: roleId,
    grantedBy: grantedBy,
    grantedAt: now,
    createdAt: now,
    updatedAt: now,
    __v: 0,
  });
  return "created";
}

let n = 0;
list.forEach(function (a) {
  const email = a.__author_email;
  const acl = a.__acl || [];
  delete a.__author_email;
  delete a.__acl;

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

  const stored = db.agents.findOne({ id: a.id });
  if (!stored) {
    print("  WARNING: agent not found after upsert — permissions not applied");
    return;
  }

  // No exported permissions (older agents.json): synthesise the owner grants the UI would
  // have created, or the agent is invisible to everyone.
  let specs = acl;
  if (specs.length === 0 && author && email) {
    specs = [
      { resourceType: "agent", principalType: "user", principal: email, permBits: 15, accessRoleId: "agent_owner" },
      { resourceType: "remoteAgent", principalType: "user", principal: email, permBits: 15, accessRoleId: "remoteAgent_owner" },
    ];
    print("  no __acl exported — synthesising owner grants");
  }

  specs.forEach(function (s) {
    const status = grant(stored._id, s, author);
    print("  perm " + status + ": " + s.resourceType + " / " + s.principalType + ":" + s.principal +
          " permBits=" + s.permBits);
  });
});
print("done: " + n + " agent(s)");
