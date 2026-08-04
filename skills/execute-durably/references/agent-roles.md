# Durable agent roles

Use the smallest set of roles needed by the packet graph. Custom agent TOML files are optional; the role contracts still apply when delegating to built-in workers.

Every assignment must declare the objective, minimum context, owned paths,
permissions, expected result, executable check, and stop conditions. Every
worker response must report status, changed paths, commands actually run with
exit codes, blockers, and remaining risks. Missing fields are an invalid result,
not implied success. Workers cannot spawn descendants, verify their parent, or
write outside the explicit assignment; depth-two subdivisions are read-only.

## Executor

Give the executor exactly one packet, its owned paths, dependencies, invariants, and declared checks. It may modify only owned paths. Require exact commands and observed results, and stop if another ownership boundary is needed.

## Test writer

Use a separate test writer only when a real pre-fix failure can be demonstrated without overlapping the executor's active ownership. Require the same focused command for RED and GREEN. A syntax error, broken fixture, or invented output is not RED evidence. If separation would create overlapping edits, keep the test and implementation with one executor and preserve the recorded red/green cycle instead.

## Verifier

Give a fresh verifier the objective, relevant diff, criterion, and receipts without an expected verdict. Require read-only inspection and a `confirmed`, `rejected`, or `inconclusive` result grounded in current evidence. A different identifier does not provide independence when the verifier has been shown the desired conclusion.

The verifier must be distinct from every executor and test writer whose result
it reviews. Launch it only after the integrated state is ready and implementation
workers have released a host slot.

## Investigator

Give the investigator a question, not a remedy. It establishes a mechanism by
running non-mutating commands and reports the exact commands, the files that
carry the mechanism, and the searches that came back empty. It cannot write, so
it is one of the three roles the policy will place at depth two. A diagnosis
that arrives already committed to a fix is the outcome this role exists to
prevent: route the fix through an executor afterwards, with its own ownership.

## Researcher

Use the researcher when the answer is outside the working tree. Require every
finding bound to its source and to the version that source describes, because an
API fact silent about its release is the failure mode here. Sources that
disagree are reported as disagreement, not resolved by the worker. It holds no
execution tool, so it is read-only without needing a disposable checkout.

## Reviewer

Use the reviewer for a perspective the author should not supply, or for several
independent angles on one diff. It returns findings against a named standard and
never a completion verdict — that verdict is the verifier's, and a reviewer
granted execution would be doing the verifier's job without its containment.
"Nothing found" is a valid result; a manufactured minor finding is not.

## Where these roles come from

The six roles ship with the plugin and are also encoded as repository-scoped
development definitions. Which of them the host actually registers differs:

- Claude Code registers an enabled plugin's `agents/` directory, exposing every
  role under a plugin-scoped name: `cognitive-powers:executor`,
  `cognitive-powers:test-writer`, `cognitive-powers:verifier`,
  `cognitive-powers:investigator`, `cognitive-powers:researcher`, and
  `cognitive-powers:reviewer`. Host security rules ignore `hooks`,
  `mcpServers`, and `permissionMode` in a plugin agent file, so no role may
  depend on those fields.
- Codex does not register agents from the plugin root, and falls back to the
  built-in workers.

Address a registered role by that exact name when the host provides one, because
its declared tool set enforces the contract instead of merely describing it; the
read-only grants are the case that matters most, and the three read-only roles
are the only ones the policy will place at depth two. Naming the role in the
prompt while spawning a general-purpose worker leaves the contract entirely to
prose, which is the state this list exists to end. When no registered role
exists, apply the same contracts to a built-in worker. Never make a durable
criterion depend on a specific role being discoverable: verify what the host
actually registered, and record which one performed the work.
