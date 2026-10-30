---
name: "python-untrusted-input"
description: "Checklist and measured gotchas for Python code that loads YAML or JSON from an untrusted or semi-trusted source (duplicate keys, aliases and anchors, merge keys, nesting depth, size caps), calls HTTP or GraphQL APIs (redirect credential carry, bounded backoff and pagination, never replaying a non-idempotent call after an ambiguous outcome), opens files by path (confinement, symlinks, FIFOs), or guards network access. Load when writing or reviewing config loaders, API clients, file transports, or hostile-input tests. Not for general style or typing questions."
updated: "2026-10-29"
---

# Pack: Python untrusted-input and API-client hardening

Status: pilot pack, written 2026-10-08 from findings of 2026-10-07; refreshed 2026-10-29 (issue #69) with this repository's own review lessons (issues #43, #47 and the installer state work) and generic lessons from the SAFO guard and approval reviews (2026-10). UNVERIFIED items were not reproduced.

Untrusted means any bytes you did not write: config files, API responses, downloaded catalogs, directory entries. Most defects below were found by a reviewer who ran a hostile input; none were found by the author's tests.

## Checklist: loading YAML and JSON

1. **Safe loader only.** `yaml.safe_load` or an explicit `SafeLoader` subclass. Audit greps must also catch `full_load`, `unsafe_load`, `load(` without `Loader=`, and `yaml.load_all`. A grep that only checks `yaml.load(` misses `full_load` (measured 2026-10-07).
2. **Duplicate keys.** PyYAML silently takes the last. Detect them at compose time (override `construct_mapping`, or walk the composed node tree) and reject.
3. **Aliases, anchors, merge keys (`<<`).** Reject or cap them. Alias expansion can multiply memory (the billion-laughs shape). Detect at the event or node level, before construction.
4. **Depth.** Limit nesting at compose time, before building Python objects. For JSON, `RecursionError` and `ValueError` from deep nesting must be caught and mapped to your own error type, never leaked.
5. **Size cap before parsing.** Cap bytes read (read at most N+1 and reject if over). Cap number of nodes if the format allows large flat inputs.
6. **Validation is a security control.** Every validation rule needs a test that goes red when that rule is deleted. Run a mutation sweep: delete each validation in turn, run the suite, and list survivors (14 surviving deletions were found in one loader, 2026-10-07).
7. **Agree on defaults.** A fix that tightens one code path can make two readers of the same file disagree; check that every consumer applies the same defaults.
8. **Own error type.** Parsers must raise one documented exception type for every malformed input; a raw `OSError`, `UnicodeDecodeError`, `RecursionError` or `KeyError` escaping is a finding.

## Checklist: HTTP and GraphQL clients

1. **No credential carry across redirects.** `urllib`'s default opener follows 301/302/303 and forwards the `Authorization` header to the redirect host (reproduced 2026-10-07). Install a redirect handler that refuses redirects, or strips credentials when host, port, or scheme changes. Prove it with two loopback servers on different ports: the second must never see the header.
2. **Non-idempotent calls are never replayed after an ambiguous outcome.** After a request is sent, a timeout, 5xx, closed connection, or in-body retryable error is UNKNOWN, not failed. Mutations (GraphQL `mutation`, POST that creates) are not retried; surface "outcome unknown" as a distinct state the caller must reconcile. Retry only idempotent reads, or calls with an idempotency key.
3. **GraphQL errors can accompany partial data.** Handle `errors` alongside `data`: do not drop partial data silently, do not retry a mutation because the body says `RATE_LIMITED`, and treat a malformed body and `INSUFFICIENT_SCOPES` as distinct, reported outcomes.
4. **Bounded backoff.** Cap attempts and total sleep. Parse `Retry-After` defensively: reject `nan`, `inf`, negative, and absurdly large values; honor an upper bound.
5. **Bounded pagination.** Cap pages. Handle `hasNextPage: true` with a null or repeated cursor (stop, do not loop).
6. **Timeouts everywhere**, including connect and read. Cap response size.
7. **Secrets** come from the environment or the secret manager, never argv, logs, or error messages; redact before logging a request.
8. A three-fix pattern was observed: each fix closed the reported symptom and left a sibling hole. After a fix, enumerate the sibling cases (see the table) before declaring it done.

## Checklist: paths, files, sockets

1. **Path confinement.** Resolve and compare against the root using path components, not string prefix. Open with `O_NOFOLLOW` and a directory fd (`dir_fd`) where the platform supports it; reject symlinks and `..`. An unsafe path is a typed error, not a raw `OSError`.
2. **FIFOs and special files.** Opening a FIFO can hang forever; use `O_NONBLOCK` for the open and check `S_ISREG` on the fd (`os.fstat`).
3. **Atomic writes.** Temp file in the same directory, `fsync`, rename, `fsync` the directory; `flock` for single-writer state. UNVERIFIED on exotic filesystems.
4. **Network guard completeness.** A test fixture that forbids sockets must cover the whole surface: `connect`, `connect_ex`, `sendto` (2 and 3 argument forms), `sendmsg`, `gethostbyname`, `gethostbyname_ex`, `getaddrinfo`. The 3-argument `sendto(data, flags, address)` survived one fix round (2026-10-07).

## Checklist: types, text and regexes

1. **Exact types first.** Check every field, key, list member and container with `type(x) is str` (not `isinstance`: a subclass can override `__eq__` or `__hash__`, and `bool` is an `int`) before any comparison, membership test, regex or hashing. A hostile `__eq__` that raised escaped a validator as a `RuntimeError` (DoIA state store review).
2. **Never call `str()`, `repr()` or `format()` on a foreign object**, including in an error message. Render only an exact `str` or `int` and show `?` otherwise; a `__str__` that raised `SystemExit` escaped a handler (same review).
3. **Text identity.** Compare names after NFC normalization and `casefold()`; two distinct names can collide on a case-insensitive or normalizing filesystem. Reject invisible, default-ignorable and control characters and non-ASCII spaces in identifiers and text fields (benchmark corpus review, #47).
4. **Regexes on untrusted text.** Check patterns for exponential backtracking when you load them, run every match under a time limit, and cap the input length first. Duplicate JSON keys are an error too (`object_pairs_hook`).

## Checklist: archives and output caps

1. **Extract archives by hand.** No `TarFile.extract` or `extractall`: write each regular member through a confined path, refuse links, devices and special members, and cap member count, member size, expanded size and nesting depth (#47).
2. **Bounded output everywhere.** Cap what you read from a file, a socket, a subprocess pipe and a git stream; over the cap is an error, not a truncation that parses.
3. **Resource bounds are tested** with an input one byte over the cap and a negative case that proves the cap test bites.

## Checklist: subprocesses

1. **Argv list, never `shell=True`.** Pass the program and arguments as a list; no string built from untrusted text reaches a shell. Use `--` before untrusted operands.
2. **Killable worker.** Start the child in its own session (`start_new_session=True`) and kill the whole process group on a timeout, an error or an interrupt; killing only the child leaves grandchildren running (#43, #47). Test it with a grandchild that would write a marker file after the timeout.
3. **The worker limits itself and dies with its parent.** In the child set `RLIMIT_CPU` (and address-space or file-size limits where they fit) before running untrusted work, and use `PR_SET_PDEATHSIG` (or a supervisor that reaps) so it dies with its parent. A wall-clock timeout alone does not stop a busy loop from burning CPU until the kill lands.
4. **Bounded output.** Read stdout and stderr through a cap and kill the group when it is exceeded; never `communicate()` on an unbounded child.
5. **No inherited secrets.** Build the environment from an allowlist (a fresh `PATH`, an empty `HOME` inside a scratch directory); never pass `os.environ` through. Tool config (git, pip) from the user's home is also an input: switch it off.
6. **Isolated interpreter flags.** Run a Python child as `python3 -I` (isolated: no user site, no `PYTHON*` variables, no current directory on the path) and keep scripts and untrusted files in different directories.
7. **A timeout kills and reports.** A signal death is a crash, never a red test result; do not map it to an ordinary exit code.

## Checklist: guards, parsers and logs

Measured in the SAFO guard and approval reviews (2026-10); one line each, no bypass detail.

1. **A guard and the real parser share one definition.** If a guard decides what a command, path, term or file means with its own scanner, it will disagree with the real parser on some input. Call the same function, or test them against one shared corpus.
2. **Fail closed on timeout and on anything unscannable.** A scan that ran out of time, depth or input is a refusal with its own reason, never an allow; a marker or override is not honored after it.
3. **Regex and term shape limits.** Cap length, count and character set of user-supplied terms and patterns, and reject shapes the guard cannot judge. A shape limit keeps a check short; it does not prove the content safe, so say so.
4. **Append-only logs are tamper-evident, not tamper-proof.** Hash-chain each record, append under a lock with `fsync`, and say plainly that someone who rewrites the whole file and its head can still forge it.
5. **Test-only seams are not reachable from the CLI.** An injectable clock, path or transport must not be settable by an option or environment variable; keep it in the test module, and test that the CLI refuses it.

## How to verify (commands)

- Hostile YAML files in the scratch directory: duplicate keys, `&a`/`*a` chains, `<<: *a`, 10,000-deep nesting, a 100 MB file, a tab/BOM/encoding oddity; run the loader on each and confirm one typed error each.
- Loader audit: `grep -rnE 'yaml\.(load|full_load|unsafe_load|load_all)\b' src | grep -v safe_load`.
- Redirect leak: start two `http.server` instances on loopback ports, have server A answer 302 to server B, call the client with a token, print B's received headers.
- Mutation replay: stub the transport to time out after sending a mutation; assert exactly one send.
- Mutation sweep: for each validation, delete it in a scratch copy and run the suite; any green run is a survivor.
- Revert check: undo the fix, run the new test, expect red; restore, expect green.
- Gate: run the repo's full gate with the exit code captured (`> log 2>&1; echo "exit=$?"`).
- Foreign-object probe: pass a subclass whose `__eq__`, `__hash__` and `__str__` raise; the loader must return its own error type.
- Subprocess probe: a child that forks a grandchild writing a marker after the timeout, and one that spins on the CPU; assert the marker never appears and the group is gone.
