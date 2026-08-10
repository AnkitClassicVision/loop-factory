# What this failure mode is actually called

Synthesised 2026-08-10 from three independent Ringer lanes with different domain
memories (SRE/observability, testing/formal verification, safety engineering),
each required to give every term a definition, a provenance a human can judge,
and an explicit statement of where the term does NOT fit. Reports:
`/mnt/d_drive/ringer-work/failure-mode-terminology-r1{,b}/`.

## The verdict on "hollow automation"

I coined it. Do not put it in a runbook as if it were standard. **Every lane,
independently, said no single established term covers the full compound** — but
each of the three properties has real, established vocabulary, and the whole is
adequately named by one widely recognised term plus a qualifier.

Use the real words. Keep a coined phrase only as a local nickname, and always
define it in terms below so a stranger or a future model can follow.

## The canonical name

**Silent failure** — the system does not perform its function and emits no
actionable fault signal. All three lanes chose this as the primary runbook term
because it is universally recognised and immediately actionable.

Two refinements worth carrying:

- **Fail-silent** (distributed systems and safety engineering): the classic
  contrast is *fail-stop* (a component halts loudly and observably) versus
  *fail-silent* (it stops producing correct output while emitting nothing that
  distinguishes it from idle). The fix direction is literally "make it fail-stop."
- **Dangerous undetected (DU) failure** (IEC 61508 functional-safety
  vocabulary): a protective or productive function is unavailable on demand AND
  the built-in diagnostics cannot reveal it. Use this when the audience is
  safety- or compliance-minded; it carries a standard behind it.

**The cleanest formal statement of what happened, from the SRE lane:** *safety
was preserved, liveness was violated.* Fail-closed design stopped anything
harmful from being sent; it did not guarantee useful progress, and it did not
guarantee the failure would be detectable. Those are two different properties,
and conflating them is what made silence look like health.

## The three properties, each with its real name

**(a) Every part verified, the joint never executed.**
- **Integration testing gap** — the missed control. Universally understood.
- **Interface contract violation** — the defect itself.
- **Contract testing** (consumer-driven contracts) — the named practice that
  prevents it: the caller's expectations are asserted against the callee's real
  interface.
- **Latent condition** (James Reason, accident causation) — the safety framing:
  a defect present and dormant until the conditions to expose it arrive.

**(b) The error signal was identical to a legitimate benign outcome.**
- **In-band error signaling** — errors carried in the same channel and value
  space as ordinary results.
- **Sentinel value collision** / **semipredicate problem** — a returned value
  that cannot distinguish an error from a valid result. Our case exactly: exit
  status 2 meant both "usage error" and "no eligible candidate today."
- **Error masking** — the caller's interpretation destroys the distinction.
- **Observational indistinguishability** — no observer could tell the two states
  apart, which is why no monitor could have caught it.
- **Mode confusion / mode error** (aviation human factors) — the operator, human
  or machine, believes the system is in a different mode than it is.

**(c) It reported healthy while producing nothing, indefinitely.**
- **Liveness violation** — the formal complement to safety: nothing bad happened,
  but nothing good ever will either.
- **Omission failure** — the expected artifact was never produced.
- **Gray failure** — internal health signals and actual usefulness disagree. Use
  as a refinement, not alone: the classic gray-failure pattern involves multiple
  observers disagreeing, which we did not have.
- **Automation complacency / automation bias**, and **normalization of deviance**
  (Diane Vaughan) — why it survives once present. A quiet lane becomes the
  expected state, and its quietness stops being evidence of anything.
- **Vacuous pass / vacuous satisfaction** (formal verification) — a check that
  passes without ever exercising what it claims to verify. This is the name for
  what my own checks did.

## One sentence a stranger can act on

> A silent, fail-silent liveness failure: an interface-contract violation at an
> untested joint, whose error was in-band and collided with a benign result, so
> the caller masked it and the automation reported a healthy no-op forever.

## How to spot it, cheapest first

1. **Ask what it looks like when it does nothing.** If "did nothing
   successfully" and "broke" produce the same observable — same code, same log
   line, same silence — the system is already capable of failing silently.
   No incident required to answer this; ask it at design time.
2. **Demand the artifact, not the status.** A status is a claim about a check; an
   artifact is evidence about the world. "Show me the last thing it produced."
3. **Alert on absence, not just on errors.** The established control is a **dead
   man's switch** (or heartbeat/watchdog): an alarm that fires when an expected
   event does NOT arrive. Error-only monitoring is blind to omission by
   construction.
4. **Proof test on a schedule.** From functional safety: periodically exercise
   the function end to end to reveal dangerous undetected failures, because a
   protective function that is never demanded is indistinguishable from a broken
   one.
5. **Treat a flat line as a finding.** An output counter that never moves is the
   signature. Zero is a claim and must be proven, exactly like the loop-drive
   contract already requires of the podcast loops.

## How to design so it cannot happen

1. **Test the seam, not the sides.** Whenever two components are built
   separately — especially by two different workers or two different sessions —
   one executed test must run the caller invoking the callee for real. This is
   the single control that would have caught our defect in seconds.
2. **Make failures out-of-band and distinct.** Never let an error share a value
   space with a legitimate result. If the interface is a process exit code,
   reserve distinct codes and make the **written artifact the authority**: no
   receipt means failure, whatever the exit code said.
3. **Fail-stop over fail-silent.** When in doubt, halt loudly. A noisy stop costs
   an interruption; a quiet stop costs months.
4. **Assert liveness explicitly.** Safety requirements ("never send to a cold
   prospect") and liveness requirements ("at least one draft per week, or a
   proof no legal candidate existed") are different, and only the first tends to
   get written down.
5. **Make every check prove it can fail.** Watch it go red on the real defect
   before trusting it green. A check never observed failing is a vacuous pass
   waiting to happen.

## What genuinely has no established name

All three lanes agreed: there is no single canonical term for the *compound* —
a fail-closed system that therefore looks safe, combined with a status collision
that partial testing can never catch, producing an indefinite healthy no-op. The
safety literature's cases assume a human demand or a periodic proof test will
eventually surface a dangerous undetected failure; here the operator's own
monitoring logic was the deceived diagnostic.

Two smaller gaps, honestly flagged by the lanes rather than papered over:
"error-code semantic collision" has no verified canonical name of its own
(semipredicate problem is the closest and comes from function return values, not
process exit codes), and the covert/patent fault distinction was marked
UNCERTAIN on attribution.

If a local nickname is genuinely wanted, define it as: *silent liveness failure
at an untested seam* — and note in the same breath that the component words are
the standard ones.
