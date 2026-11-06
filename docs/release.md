# The release path

Reference for the engineer who adds a repository's `release.yml` or reads a refusal of the release gate.

## The path

A release is dispatched on `main` with the version alone. `release-gate.yml` (quality's reusable workflow) only reads and
refuses before any tag exists; the caller's own build then proves its source carries the version. The tag job is not part
of the path yet.

## A caller's `release.yml`

```yaml
name: Release

on:
  workflow_dispatch:
    inputs:
      version:
        description: The version to release, X.Y.Z
        required: true
        type: string

concurrency:
  group: release
  cancel-in-progress: false

permissions: {}

jobs:
  gate:
    permissions:
      actions: read
      checks: read
      contents: read
      pull-requests: read
    uses: overnightworks/quality/.github/workflows/release-gate.yml@<commit sha> # v<tag>
    with:
      version: ${{ inputs.version }}

  build:
    needs: gate
    runs-on: ubuntu-latest
    permissions:
      contents: read
    env:
      VERSION: ${{ inputs.version }}
      COMMIT: ${{ needs.gate.outputs.commit }}
    steps:
      - uses: actions/checkout@<commit sha> # v<tag>
        with:
          ref: ${{ needs.gate.outputs.commit }}
          persist-credentials: false
      # the caller's own proof that the source carries "$VERSION"
```

Each line has a reason:

- `permissions: {}` on the workflow and one block per job: a job holds only what it reads. The gate reads CI's run
  (`actions`), the merged pull request's check runs (`checks`), the head and tags (`contents`) and which pull request
  merged the commit (`pull-requests`); a call that grants less fails.
- `version` is the only input and reaches a step only through `env`, never inside a `run` script.
- `needs: gate` makes the build wait for the gate; without it the build runs even when the gate refuses.
- The build checks out `needs.gate.outputs.commit`, the commit the gate judged, never `main`, which may have moved.
- `quality`'s own release uses GitHub's same-repository form, `uses: $/.github/workflows/release-gate.yml`, so it calls
  the gate of its own commit. Every other repository pins the commit sha of a quality tag and names the tag in a comment.
- quality's build also tags the gated commit in its own checkout (the tag never leaves the runner) and runs
  `scripts/verify_release.py`, which proves the installed version equals the tag.

## The version

A library or tool releases `X.Y.Z`, each part a number without leading zeros, tagged `vX.Y.Z`. An app's form is decided
with the image workflow; until its cut-over an app keeps its dated tag `vYYYY.MM.DD.N` on a main commit, and the gate
neither judges nor counts that tag.

## Refusals

Each refusal prints one line on stderr and fails the gate; no tag exists yet.

| Line | What to do |
|---|---|
| `Refused: dispatched from <ref>, a release is dispatched from main.` | Dispatch with `--ref main` (the default). |
| `Refused: the version is not X.Y.Z, each part a number without leading zeros.` | Give `1.2.3`, not `v1.2.3`, `1.2` or `01.2.3`. |
| `Refused: main moved from <sha> to <sha> after the dispatch.` | Dispatch again; the release is of main's head at the dispatch. |
| `Refused: the tag v<version> exists.` | Choose the next version; a tag is never moved. |
| `Refused: <sha> already carries the release tag <tag>.` | The commit is released; release a later commit. |
| `Refused: CI has no push run on <sha>.` | Wait for CI's push run on main, or find why none ran. |
| `Refused: CI's push run <id> is <status>.` | Wait until it completes. |
| `Refused: CI's push run <id> concluded <conclusion>.` | Fix main; release the next green commit. |
| `Refused: the quality / verdict of CI's push run <id> is <conclusion>.` | The gate's verdict job did not succeed; fix it on main. |
| `Refused: no single pull request merged <sha> into main.` | Every main commit comes from exactly one merged pull request; release a commit that does. |
| `Refused: pull request #<n> merged with quality / verdict <conclusion>; release the next commit merged with a green verdict.` | The commit was merged past a red or missing verdict (an emergency merge). Only that commit is refused; its content ships with the next commit merged green. A later re-run does not lift this: only check runs finished by the merge count. |

The check name `quality / verdict` and the CI file name `ci.yml` are fleet contracts: the caller job of
[`gate.yml`](gate.md) is named `quality`.

## Concurrency

One group, `cancel-in-progress: false`: a release never cancels a running release. GitHub keeps at most one pending run
per group, and a newer pending dispatch cancels the older.

## Residual

The gate is no boundary. A `v*` tag can still be created without it: the organisation ruleset restricts deletion and update
of tags, not creation.

## quality's own release

A dispatch of quality's `release.yml` gates and builds and creates no tag; quality tags by hand (see `AGENTS.md`). The operator dispatches with:

```sh
gh workflow run release.yml -R overnightworks/quality -f version=X.Y.Z
```

An agent dispatches only on his explicit yes for that one version.

## The standard

Ruled by the operator: every project is released on one checked path, and every release starts with the same command and only the version: gate,
then the project's own build, then tag; the gate exists once, in quality. Covered are every
repository that publishes a release (agent-runtime, aco, appkit, quality, speech-runtime, webauth) and every app that ships an image
through the platform (hopin, presentator, speech-runtime, songmaker, platform's Keycloak image). The trigger is a repository's first release after the quality tag that ships the workflows it needs.

A deviation is asked as marketplace `docs/standards.md` says: the covered head names the enduring harm with its
uncertainty, quality's head reviews it, and the operator rules it; the ruling is recorded at the owner, overnightworks/quality.
