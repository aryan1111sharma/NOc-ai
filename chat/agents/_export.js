// Export all LibreChat agents as config-as-code (run inside the mongo container by
// export-agents.ps1 / .sh). The owner is stored as __author_email — portable across fresh
// databases, where ObjectIds differ. Mongo internals (_id, __v) are dropped. Restore with
// restore-agents.ps1 / .sh. See chat/RUNBOOK.md.
const out = [];
db.agents.find({}).sort({ id: 1 }).forEach(function (a) {
  let email = null;
  if (a.author) {
    const u = db.users.findOne({ _id: a.author });
    if (u) email = u.email;
  }
  delete a._id;
  delete a.__v;
  delete a.author;
  a.__author_email = email;
  out.push(a);
});
print(EJSON.stringify(out, null, 2));
