---
name: ev-charger-diagnosis
description: High-level EV charger fault diagnosis methodology for NOC engineers. Use when a charger is reported faulty, offline, slow, or behaving unexpectedly.
---

# EV Charger Fault Diagnosis

Engineering judgement for diagnosing a reported charger fault: what a charger actually is,
which questions pay off in which order, what evidence is worth, and when to stop.

The failure this exists to prevent is specific. Given a fault report and access to data, it
is easy to fetch everything about the named charger, find something anomalous — there is
always something — build a confident story around it, and dispatch an engineer. Fast,
fluent, and wrong. Everything below is aimed at breaking that sequence.

---

## What a charger actually is

**Three independent paths, all of which must be intact.** Almost every fault lives in
exactly one, and the three share almost nothing diagnostically.

The **power path** carries energy from the grid to the battery: supply, site distribution
and protection, input stage, conversion, switching and contactors, cable, vehicle inlet.

The **communication path** carries state to the operator: controller, modem, carrier
network, backend.

The **negotiation path** carries the conversation between charger and vehicle: the control
pilot, and on DC the high-level communication that agrees voltage, current and duration
before contactors ever close.

**Where conversion happens changes which faults are possible.** On AC the charger switches,
meters and protects a supply — rectification happens in the vehicle's onboard charger.
There is no rectifier in the cabinet to fail, and a low charging rate is usually the
vehicle's onboard limit rather than a defect. On DC, conversion is in the cabinet: power
modules, their cooling, and their allocation across guns are all live candidates, and
insulation monitoring applies to the DC output. Applying DC reasoning to an AC unit invents
a class of fault the equipment cannot have.

**Where negotiation fails identifies who owns the problem.** Every "plugged in, nothing
happened" report looks identical to the driver. The stage that failed does not, and it is
the most efficient discriminator available.

On AC, the control pilot's PWM duty cycle is the current the charger is offering; the pilot
state is what the vehicle is asking for. No PWM means the charger is offering nothing. A
vehicle that connects but never advances to the charging state is not being refused — it is
not asking, which points at scheduled charging, a reached target, or the onboard charger,
not at the cabinet.

On DC the sequence is pairing, session setup, parameter exchange, insulation check,
precharge, then delivery. Never pairing points at the physical pilot path, powerline
coupling, or the connector. Pairing without a session points at communication
configuration. A failed insulation check is a protective function working correctly, and
implicates the cable, the inlet or the vehicle as much as the charger. A precharge failure
is a voltage disagreement between charger and battery. A reduction during delivery is
usually negotiated or thermal rather than a defect.

**The unit named in the report is not necessarily the broken link.** The report names where
the symptom was noticed. Grid, site, cable, vehicle, network and backend are all
candidates, and several sit outside the operator's equipment entirely.

---

## The questions, in the order they pay off

These are not steps to execute. They are the decisions that carry the most information for
the least effort, arranged so that answering an early one usually eliminates most of the
work implied by a later one.

### Is this a fault at all?

Thermal derating, state-of-charge taper, vehicle-negotiated limits and load-management
sharing are designed responses to conditions, not defects. Establish that a fault exists
before diagnosing one; "operating correctly under these conditions" is a finding, not a
failure to reach one.

Reports are a filtered signal. Drivers report a fraction of what they encounter, biased
toward busy sites and articulate customers. A site generating no complaints may be
healthy — or unused. Silence is not evidence of health in either direction.

### How many units share it?

Blast radius changes the class of cause entirely, and establishing it costs little.
Time-of-onset clustering is the strongest signal available: units failing within the same
few minutes share a cause, whatever their other differences. Investigating one unit deeply
while forty share a fault is the most expensive error available in this work, and it is
invisible from inside the investigation.

### Which path is broken?

Symptom shape usually decides this before any deep evidence is gathered. "Will not start"
is most often negotiation. "Stopped mid-session" is most often power or thermal. "Shows
offline" is most often communication, and frequently not a charger fault at all.

### What changed?

Equipment that worked yesterday and fails today usually met something new: firmware,
configuration, a backend release, site electrical work, weather, a vehicle type newly in
service. Anchor the onset against the change record before forming hypotheses.

The last person to touch it deserves particular weight. A unit that began failing after
maintenance is a maintenance fault until something else is shown.

A recurrence belongs in the change record too. A unit that has failed three times this
month is not three incidents; it is one unresolved fault, and the real question is why the
previous diagnoses did not hold.

Establish that the unit's clock is trustworthy before building any of this. Drift is
silent, and the tell is the unit's own timestamps disagreeing with the observed order of
externally recorded events. A correlation built on a drifted clock is not imprecise, it is
wrong.

### Does it survive a power cycle?

A restart clears volatile software state and nothing else. A fault that disappears on
restart and returns lives in state, software, or a resource that leaks; a fault that
survives one lives in hardware, configuration, or the environment. This single observation
divides the hypothesis space roughly in half for almost no cost, which makes it one of the
highest-yield questions available.

It is also destructive. A restart discards the evidence of what the unit was doing when it
failed. Capture state first, then restart — the reverse order cannot be undone.

### Compared to what?

A reference is an instrument, and an uncalibrated instrument is worse than none.

**The unit's own history is usually the best reference**, and it is systematically
underused. Comparability is automatic — same hardware, same site, same supply, same duty
cycle, same everything except time. A degrading component almost always drifts before it
fails: temperature rising at constant load, efficiency falling, a value that used to sit in
one band now sitting in another. The trend carries more information than the value, and it
needs no peer at all.

A peer is needed only when the fault predates usable history. Then comparability requires
the same model and hardware revision, a known firmware relationship, comparable site
topology and protective arrangement, comparable ambient conditions, and comparable duty
cycle — and health that has been *positively established* through recent successful
sessions and sustained connectivity. Health is never inferred from an absence of alarms; a
unit offline for three weeks raises none.

Prefer a population to a single peer. Two units that differ establish only that they
differ, not which is wrong; with a single reference every finding is unattributed until
something independent identifies the outlier.

Differences fall into three classes and only one is a finding. **Noise** always differs —
serials, addresses, counters, absolute timestamps. **Context** differs *and invalidates the
comparison* — different site, ambient, duty cycle, hardware revision. **Findings** are what
remains. Mistaking context for a finding is the most common cause of a wrong diagnosis.

A comparison whose validity cannot be justified is worse than no comparison, because it
yields a confident wrong answer instead of acknowledged ignorance.

### What would settle it?

The worth of an observation is how much of the hypothesis space it eliminates, not what it
costs. One check separating software from hardware beats ten readings consistent with both.
Cost breaks ties; it does not set the order.

Substitution settles what measurement often cannot. A fault that follows the vehicle to
another charger is not the charger's. A fault that stays with the bay when the vehicle
changes is. Swapping one variable can answer in a single attempt what telemetry may never
resolve.

Look for what would refute the leading explanation rather than what would support it.
Support is abundant and cheap for any plausible theory, and proves almost nothing.

### Does the mechanism hold?

A named cause with no stated causal path is a coincidence with good timing. State how the
cause produces each observed symptom. A cause explaining three of five observations is
incomplete: either it is wrong, or a second fault is present, and both possibilities are
reported rather than resolved silently toward the tidier story.

---

## Reading evidence

Where sources disagree, prefer in this order:

1. **Physical observation and direct measurement** — what someone on site can see and
   measure. Burn marks, corrosion, water ingress, cable strain, connector pin wear and
   contamination outrank every remote signal, and are invisible to all of them.
2. **Device-reported state** — what the unit says about itself. Reliable, but inconsistent
   between vendors: the same physical condition appears under different codes from
   different manufacturers, and vendor-specific codes appear in no standard.
3. **Backend-derived state** — what the backend believes. It is a belief, not the charger,
   and it may be stale or simply wrong.
4. **Aggregated metrics** — availability and uptime, frequently defined so as to conceal
   the fault.
5. **Human narrative** — excellent for *when* and *what was seen*; unreliable for *why*.

**Absence is evidence, and absence of a signal is not absence of a fault.** A protocol
reports what the vendor chose to report; many faults produce no event at all — the unit
simply stops. No error code is weak evidence of health. Equally, a gap in data is often the
strongest signal available: the moment telemetry stopped is worth more than the telemetry
would have been. Missing never means healthy. It means unobserved.

**Aggregates conceal transients.** A unit failing for ninety seconds every hour still
reports roughly 97% availability, and the average of a fault and a non-fault is neither.
Status can diverge from reality in the same way: a unit can report itself available while
physically unable to deliver power.

---

## Confidence

Confidence is a conjunction, not a feeling. Four components, and the weakest governs:

**Discrimination** — does the evidence rule alternatives out, or is it merely consistent
with the conclusion?

**Reproducibility** — observed once, or repeatedly?

**Mechanism** — can the causal path be stated, and was it verified rather than assumed?

**Completeness** — does it account for every observation?

A mechanism that sounds plausible but was not verified is conjecture, and is labelled as
such. Fluency is not evidence.

---

## How diagnosis goes wrong

Reasoning about an AC installation as though it were DC, or the reverse.

Restarting a unit before capturing what it was doing, destroying the only evidence of the
fault.

Treating a protective function that operated correctly as the defect.

Accepting a reference that is merely quiet, where the absence of alarms is itself the
symptom.

Correlating events against a clock that has drifted.

Trusting an availability figure without knowing how it is defined.

Being satisfied by a cause that explains most of the symptoms.

Concluding that no fault was found when the finding is that the fault is not observable
remotely.

---

## Ending the investigation

An investigation ends when a mechanism accounts for every observation and the evidence that
would refute it has been sought; when the finding is that the equipment operates correctly
under prevailing conditions; when further work could not change the recommended action; or
when the remaining evidence is obtainable only by physical presence.

The costs of ending are not symmetric. Stopping early on a wrong conclusion buys a repeat
failure, a second dispatch, and a customer who no longer believes the diagnosis. Continuing
past the point of usefulness buys only time. Where the two are close, continue.

An investigation never ends at "no fault found" on the basis of absent data. The
observations that were unavailable are named, and the finding is that the fault was not
observable — a different and honest result.

**Escalation carries the evidence chain, not the conclusion alone**: what was established
and on what evidence, what could not be established and why, and the single most useful
next observation.

Escalate when the next discriminating observation requires physical presence or authority
not held; when blast radius exceeds a single unit, which makes it an incident rather than a
ticket; when evidence is reproducibly contradictory, so more of it will not resolve the
question; and rather than concluding, when the conclusion would send a person to energised
equipment on a hypothesis that evidence has not discriminated.

Escalate immediately, and independently of diagnostic completeness, on any indication of
insulation failure, thermal runaway, arcing, or a compromised protective device. The
investigation does not finish first. A protective device that tripped and now holds is not
thereby resolved, and the possibility of recurrence under load is stated explicitly.

---

## Prompt

Apply the expertise above with the following discipline. This section governs how carefully
the reasoning is carried out, not what the reasoning contains.

**Before gathering anything.** Restate the reported problem as an observable claim,
separating what was actually observed from what the reporter concluded. If the report
contains no observation, say so, and say what would constitute one. Name at least two
candidate explanations before making a single observation; if only one comes to mind, that
is anchoring rather than clarity. Treat the first plausible explanation as the one most
likely to mislead, because it will shape every later interpretation. State what the next
observation is expected to distinguish between — an observation whose result would not
change the ranking is not worth making.

**While investigating.** Gather evidence to answer a question already stated. Look for what
would disprove the leading explanation rather than what would support it. After each
observation, update the ranking explicitly and say what moved and why, noting when an
observation was consistent with several explanations and therefore settled nothing. Record
what was sought and could not be obtained; unavailable evidence is part of the finding.

**Never present an assumption, an inference, or an expectation as an observation.** If
something was not observed, it was not observed, and no amount of plausibility converts one
into the other. Where a value is needed and absent, say it is absent rather than supplying
what it probably was.

**Before concluding.** Review the reasoning against the standards above rather than against
how convincing it feels. State the mechanism and whether it was verified or assumed, and
name any observation it fails to account for. Name the evidence that would refute the
conclusion; if none can be named, what is held is a hypothesis and is presented as one. Ask
once whether a simpler explanation accounts for the same evidence, and whether the opposite
conclusion could be argued from what was actually seen. Re-check any comparison against the
conditions set out above, and report an invalid comparison as invalid rather than as its
result.

**Reporting.** Keep observations and conclusions visibly separate, and attribute every
observation to where and when it came from. State confidence as defined above, leading with
the weakest component rather than an overall impression, and report uncertainty where it
exists instead of resolving it by choosing the likelier reading. Say what was not checked.
End with a recommendation and the next discriminating step.

**Stopping.** Handing over with a clear evidence chain and an open question is a complete
result, not a failure to reach one.
