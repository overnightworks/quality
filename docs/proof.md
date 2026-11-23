# Proof and CI time

Reference for the engineer or head who adds tests or a CI job in a covered repository and asks what proof the repository owes and how fast its CI must be. The standard binds every repository that has tests; quality owns it and judges it: the rules in [rules.md](rules.md) on every pull request, the numbers in the weekly report ([commands.md](commands.md)). Each rule takes effect with the quality release that carries it, the one its section in rules.md names under "first ships"; a gate pinned below that release shows `not in <tag>` in the fleet matrix.

## The layers

| Layer | Proves | Unit of isolation | How it runs in parallel |
|---|---|---|---|
| unit | logic and its cases | the test | xdist workers in one job, always |
| integration | one adapter against its real dependency | the worker, with its own database, schema or socket | xdist workers; it may share a job with the unit layer |
| flow | what a person or an agent does at a surface, through the real entry point | the stack | shards, each with its own stack; workers inside one stack only where every test owns its data |
| probe | deployed state: cluster cells, image and network proofs | the cluster or container | parts, each with its own cluster |
| live | the deployed app through a real sign-in | the operator's PC | one run through platform's live-check command (overnightworks/platform#804), never in CI |

Flow and probe tests that share one running app turn flaky when more workers hit it, so for them parallel means more copies of the stack. A live check is a probe in operation, not a test layer: outside the gate, coverage and the fake, determinism and parallel rules, and never the only proof of logic CI can reach. A job's name says its layers; flow and probe run in jobs of their own. Every flow test keeps running in every browser and viewport its app has set up; sharding brings the time down.

## The limits

No CI job runs longer than 15 minutes, every pull request shows its CI time, and a repository whose weekly median exceeds 10 minutes breaches the standard. Every job runs on every pull request; no job is skipped because of the paths a change touches. The verdict checks it for a mandatory job: one that was skipped is red (VERDICT-8, `docs/gate.md`); the skip of a non-mandatory job is not machine-read.

## The rules a machine checks

Each is checked by quality's gate, either as a rule on the repository's files or through the verdict on the run, so the check has no timing noise.

| Rule | What it holds | Checked by |
|---|---|---|
| R1 | every job of a workflow that runs on a pull request or on a push to main declares `timeout-minutes` of at most 15; a higher value is recorded debt that only falls | `job-timeout` (TIMEOUT-1 and on) |
| R2 | every pytest call in a workflow passes `-n` with workers, or carries its reason in the rule's marker | `pytest-workers` (PYTEST-1 and on) |
| R3 | every workflow a pull request triggers groups its pull-request runs by number and cancels only those; every other run gets a group of its own, so a run on main always finishes | `pr-concurrency` (CONCURRENCY-1 and on) |
| R4 | every workflow a pull request triggers is the gate's caller, CodeQL's, or declared with its reason | `pr-workflows` (PRWORKFLOW-1 and on) |
| R5 | a sharded mandatory job keeps its name as an aggregator job that needs every shard | the verdict: a name the run holds only as shard legs is `missing  not in the run` (VERDICT-7), and dropping the name from the config is a loosening (VERDICT-13), both in `docs/gate.md`; whether the aggregator fails on a failed or skipped shard is not machine-read |
| R6 | the unit and integration layers run in random order with the seed printed | `pytest-random` (RANDOM-1 and on), `vitest-random` (VITEST-1 and on) |
| R7 | no workflow runs the live layer | `live-layer` (LIVE-1 and on): a pytest call that can select a test marked `live` is a finding. No coverage check stands beside it, since coverage is measured only inside CI runs and no workflow runs the live layer; a call behind a script is left to the live layer's own boundary (LIVE-8) |

## The rules only judgement sees

The audit skill's `tests` lens judges them per repository on the standing audit's cadence.

- One behaviour, one test, at the lowest layer that can prove it; a flow test proves a path once, and a branch it reaches gets no mirror unit test.
- Cases that differ only in data are one parametrized test; shared arrangement lives in fixtures and builders.
- A test waits for a condition through one wait helper or an event, never for a fixed time.
- The slowest tests of each layer are known: a unit test over one second or a flow test over thirty is examined.

## The numbers

The weekly report prints them in the run summary of `report.yml`, for the heads and the audit; no weekly list goes to the operator.

| Number | What it counts | Where it stands |
|---|---|---|
| M1 | a run's wall clock, slowest job and longest chain of waiting jobs; per repository the median, p90, red share, queue p90 and the jobs over 12 minutes at p90 | the verdict's `time` note (VERDICT-74 in [gate.md](gate.md)); the CI-time table (FLEET-104) |
| M2 | jobs that failed and then passed on a re-run | open: quality#953 |
| M3 | the share of red push runs on main | the `Red on main` column (FLEET-104) |
| M4 | test lines against source lines and tests per layer | open: quality#720, slice 13 |
| M5 | re-runs and failures before a job's first test step | open: quality#953 |
| M6 | skipped and fixme-marked tests | open: quality#720, slice 13 |

## The alert route

A breach reaches the operator only as a mail through the platform's alert route, never as a list. quality publishes the weekly median per repository as a file the platform reads without a credential (`site/fleet-ci-time.json`, FLEET-106 to FLEET-111); the platform's own alert route mails the operator (platform#359). The breach knows no recorded debt: every repository whose weekly median exceeds 10 minutes fires the alert each week. Until that check stands, the table in the weekly summary is the only place a breach shows.

## Where the standard bends

Only the operator rules a deviation. A job above 15 minutes is recorded debt in `quality/baseline.toml` that only falls (R1); a pytest call that cannot take `-n`, or a flow layer that cannot shuffle, carries its reason in the rule's marker line (R2, R6); the live layer has no marker, since no workflow may run it (R7); a workflow outside the gate's caller is declared with its reason (R4).

## Covered repositories

Every repository that has tests. The fleet quality measures is the organization's public, non-archived, non-fork repositories, selected once in `quality/fleet.toml` (FLEET-49); the weekly table and the matrix in the report show each repository's state.
