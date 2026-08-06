// Export all LibreChat agents as config-as-code (run inside the mongo container by
// export-agents.ps1 / .sh). The owner is stored as __author_email — portable across fresh
// databases, where ObjectIds differ. Mongo internals (_id, __v) are dropped. Restore with
// restore-agents.ps1 / .sh. See chat/RUNBOOK.md.
//
// Permissions are exported too, as __acl. LibreChat v0.8.x gates agent visibility on the
// `aclentries` collection keyed by the agent's Mongo _id — NOT on the agent document. An
// agent with no acl entries is invisible to everyone, including its own author, so an
// export that dropped them produced agents nobody could select. Principals are stored
// portably: users as emails, roles as role names, and the access role as its accessRoleId
// (agent_owner / agent_viewer / ...) rather than a database-local ObjectId.
const out = [];
db.agents.find({}).sort({ id: 1 }).forEach(function (a) {
  let email = null;
  if (a.author) {
    const u = db.users.findOne({ _id: a.author });
    if (u) email = u.email;
  }

  const acl = [];
  db.aclentries.find({ resourceId: a._id }).forEach(function (e) {
    let principal = e.principalId;
    if (e.principalType === 'user') {
      const pu = db.users.findOne({ _id: e.principalId });
      if (!pu) return; // owner no longer exists — restore would have nothing to bind to
      principal = pu.email;
    }
    let accessRoleId = null;
    if (e.roleId) {
      const ar = db.accessroles.findOne({ _id: e.roleId });
      if (ar) accessRoleId = ar.accessRoleId;
    }
    acl.push({
      resourceType: e.resourceType,
      principalType: e.principalType,
      principal: String(principal),
      permBits: e.permBits,
      accessRoleId: accessRoleId,
    });
  });

  delete a._id;
  delete a.__v;
  delete a.author;
  a.__author_email = email;
  a.__acl = acl;
  out.push(a);
});
print(EJSON.stringify(out, null, 2));
