# The release path

Reference for the engineer who adds a repository's `release.yml` or reads a refusal of the release gate.

## The path

A release is dispatched on `main` with the version alone. `release-gate.yml` (quality's reusable workflow) only reads and
refuses before any tag exists; the caller's own build then proves its source carries the version; `release-tag.yml`
(quality's reusable workflow) then creates the tag at the gated commit and publishes the release.

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
      # a build that makes release files uploads them with actions/upload-artifact

  tag:
    needs: [gate, build]
    permissions:
      contents: write
    uses: overnightworks/quality/.github/workflows/release-tag.yml@<commit sha> # v<tag>
    with:
      version: ${{ inputs.version }}
      commit: ${{ needs.gate.outputs.commit }}
      artifact: <the build's artifact name, or omit for a release without files>
      notes: <optional Markdown that opens the release notes>
```

Each line has a reason:

- `permissions: {}` on the workflow and one block per job: a job holds only what it reads. The gate reads CI's run
  (`actions`), the merged pull request's check runs (`checks`), the head and tags (`contents`) and which pull request
  merged the commit (`pull-requests`); a call that grants less fails.
- `version` is the only input and reaches a step only through `env`, never inside a `run` script.
- `needs: gate` makes the build wait for the gate; without it the build runs even when the gate refuses.
- The build checks out `needs.gate.outputs.commit`, the commit the gate judged, never `main`, which may have moved.
- `needs: [gate, build]` makes the tag wait for both; `contents: write` is granted to the tag job only, so no other job
  can write, and nothing dispatches the tag job apart from the release run.
- `commit` is the gate's output, never `github.sha`, which may have moved.
- `artifact` names an artifact of the same run. Its regular top-level files, dotfiles included, are the release's files and
  nothing else is uploaded; a file name carries no `#`, which gh reads as the start of an asset label. A build that makes
  release files turns its caches off. Omit it for a release without files.
- `notes` is optional; GitHub's generated notes follow it. A build that writes notes computes each hash from the very file
  it uploads. A head may edit the notes after publishing: an immutable release still accepts edited notes.
- `quality`'s own release uses GitHub's same-repository form, `uses: $/.github/workflows/release-gate.yml` and
  `release-tag.yml`, so it calls the workflows of its own commit. Every other repository pins the commit sha of a quality tag and names the tag in a comment.
- quality's build also tags the gated commit in its own checkout (the tag never leaves the runner) and runs
  `scripts/verify_release.py`, which proves the installed version equals the tag.

## The version

A library or tool releases `X.Y.Z`, each part a number without leading zeros, tagged `vX.Y.Z`. An app releases
`YYYY.MM.DD.N`, tagged `vYYYY.MM.DD.N`: a four-digit year, month `01` to `12`, day `01` to `31`, and `N` counting the day's
releases from 1 without leading zeros. An app's call of the gate passes `form: YYYY.MM.DD.N` beside the version; without
`form` the gate judges `X.Y.Z`. The gate counts only tags of the caller's form as release tags, so a tag of the other form
on the commit never refuses it. The image takes the version as a Docker tag.

## An app's image

An app's build calls `image.yml` once per image, after the gate, and pushes it to GHCR with an attestation:

```yaml
  image:
    needs: gate
    permissions:
      attestations: write
      contents: read
      id-token: write
      packages: write
    uses: overnightworks/quality/.github/workflows/image.yml@<commit sha> # v<tag>
    with:
      version: ${{ inputs.version }}
      push: true
    secrets:
      build-secrets: ${{ secrets.HF_TOKEN_BUILD_SECRETS }}
```

The secret's name is illustrative.

- The caller job grants all four permissions, even with `push: false`; GitHub refuses a call that grants less.
- The image is `ghcr.io/<owner>/<repository>`, lowercased; `image` adds a sub-name for a repository with several images
  (`worker` gives `ghcr.io/<owner>/<repository>/worker`).
- `version` is the image tag as given; only a valid Docker tag passes.
- The tag job names each image job in its `needs` (for example `needs: [gate, build, image]`), so the tag and the
  release follow only pushed images.
- `context` (default `.`), `file` (default the context's `Dockerfile`) and `target` (default the last stage) select the
  build.
- `build-args` holds `NAME=value` lines. The provenance records them, so a secret goes into `build-secrets` as
  `id=value` lines and is read with `RUN --mount=type=secret,id=<id>`. Pass `build-secrets` by name; never
  `secrets: inherit`.
- The image is built for `linux/amd64` on a GitHub-hosted runner, from the dispatched commit, the one the gate judged.
- BuildKit adds its provenance (`mode=max`) and an SBOM to the image; with `push`, `actions/attest` adds a signed SLSA
  build provenance, signed by `image.yml`. A consumer verifies it with `gh attestation verify oci://<ref> --repo
  <owner>/<repository> --signer-workflow overnightworks/quality/.github/workflows/image.yml --source-ref refs/heads/main
  --predicate-type https://slsa.dev/provenance/v1`.
- `cache: true` reads and writes the GitHub Actions build cache; it stays off for a release.
- `submodules: true` checks out the commit's git submodules, one level. The job's token fetches them from github.com and
  is not persisted after the checkout, so a submodule that token cannot read fails there. The commit pins each one.
- The outputs, set only with `push`: `digest` (`sha256:<hex>`) and `ref`
  (`ghcr.io/<owner>/<repository>[/<image>]:<version>@<digest>`).
- The package takes the access of the caller's repository; a deployment that pulls it needs read access set on the
  package.

## Refusals

Each refusal of the gate prints one line on stderr and fails the gate; no tag exists yet.

| Line | What to do |
|---|---|
| `Refused: dispatched from <ref>, a release is dispatched from main.` | Dispatch with `--ref main` (the default). |
| `Refused: the version is not X.Y.Z, each part a number without leading zeros.` | Give `1.2.3`, not `v1.2.3`, `1.2` or `01.2.3`; an app's call passes `form: YYYY.MM.DD.N`. |
| `Refused: the form <form> is neither X.Y.Z nor YYYY.MM.DD.N.` | Fix the `form` the caller's `release.yml` passes. |
| `Refused: the version is not YYYY.MM.DD.N, month 01 to 12, day 01 to 31, N a number from 1 without leading zeros.` | Give `2026.11.06.1`, not `v2026.11.06.1`, `2026.11.6.1`, `2026.13.06.1` or `2026.11.06.0`. |
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

These refusals stop the attempt before publishing; none creates or changes a tag or release:

| Line | What to do |
|---|---|
| `Refused: files were fetched, but no artifact was named.` | Name the artifact, or fetch nothing. |
| `Refused: the artifact <name> brought no file.` | The build uploaded nothing under that name; fix the build. |
| `Refused: the artifact <name> holds <entry>, which is not a regular top-level file.` | Upload regular top-level files only. |
| `Refused: the file <name> has a # in its name, which gh reads as the start of an asset label.` | Rename the file in the build. |
| `Refused: the tag v<version> exists at <sha>, not at the gated commit <sha>.` | Choose the next version; a tag is never moved. |

Re-run the failed jobs after a failed publish: the tag job runs whole again, keeps the tag at the commit and completes the
release. "Re-run all jobs" runs the gate again, which refuses the existing tag. A tag left without a release by a defective
publish step is completed by hand with `gh release create v<version> <files> --verify-tag`.

## Concurrency

One group, `cancel-in-progress: false`: a release never cancels a running release. GitHub keeps at most one pending run
per group, and a newer pending dispatch cancels the older.

## Residual

The gate is no boundary. A `v*` tag can still be created without it: the organisation ruleset restricts deletion and update
of tags, not creation.

An app's tag job waits for its image jobs, so a failed tag job leaves a pushed image in GHCR without a tag or release.
quality builds no guard for this: platform's CI is the one guard, and merges a version bump only with the app's
tag or release at that version and the image's attestation.

While the draft release is open, users and workflows with write permission can change it and its assets, so "exactly the
build's files" holds against cooperative writers only. A killed runner can leave a stale draft, and gh can refuse after a
completed publish; both are completed or deleted by hand.

## quality's own release

A dispatch of quality's `release.yml` gates, builds, tags and publishes a release without files, with GitHub's generated
notes. A patch on a release branch is still tagged by hand (see `AGENTS.md`). The operator dispatches with:

```sh
gh workflow run release.yml -R overnightworks/quality -f version=X.Y.Z
```

An agent dispatches only on his explicit yes for that one version.

## The standard

Ruled by the operator: every project is released on one checked path, and every release starts with the same command and only the version: gate,
then the project's own build, then tag; the gate exists once, in quality. Covered are every
repository that publishes a release (agent-runtime, aco, appkit, quality, speech-runtime, webauth) and every app that ships an image
through the platform (hopin, presentator, speech-runtime, songmaker, skat, platform's Keycloak image). The trigger is a repository's first release after the quality tag that ships the workflows it needs.

A deviation is asked as marketplace `docs/standards.md` says: the covered head names the enduring harm with its
uncertainty, quality's head reviews it, and the operator rules it; the ruling is recorded at the owner, overnightworks/quality.
