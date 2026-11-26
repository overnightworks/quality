# The release path

Reference for the engineer who adds a repository's `release.yml`, reads a refusal of the release gate or depends on another fleet package.

## The path

A release is dispatched on `main` with the version alone.

- GATE-1: `release-gate.yml` (quality's reusable workflow) only reads
  and refuses before any tag exists; the caller's own build then proves its source carries the version;
- TAG-1: `release-tag.yml` (quality's reusable workflow) then creates the tag at the gated commit
- TAG-2: and publishes the release.

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
          persist-credentials: false
      - name: Refuse unless the checkout is the commit the gate judged
        run: |
          head=$(git rev-parse HEAD)
          if [ "$head" != "$COMMIT" ]; then
            echo "Refused: the checkout is $head, not the gated commit $COMMIT." >&2
            exit 1
          fi
      # the caller's own proof that the source carries "$VERSION"
      # a build that makes release files uploads them with actions/upload-artifact
      # a build that also publishes a page uploads it with actions/upload-pages-artifact

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

  # optional: a release that publishes a static page
  publish:
    needs: [gate, build, tag]
    runs-on: ubuntu-latest
    timeout-minutes: 5
    permissions:
      pages: write # deploys the Pages artifact
      id-token: write # proves to Pages that this run made the artifact
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    steps:
      - name: Deploy to GitHub Pages
        id: deployment
        uses: actions/deploy-pages@<commit sha> # v<tag>
```

Each line has a reason:

- RELEASE-1: `permissions: {}` on the workflow
- RELEASE-2: and one block per job: a job holds only what it reads. The gate reads CI's run
  (`actions`), the merged pull request's check runs (`checks`), the head and tags (`contents`) and which pull request
  merged the commit (`pull-requests`); a call that grants less fails.
- RELEASE-3: `version` is the only input
- RELEASE-4: and reaches a step only through `env`, never inside a `run` script.
- RELEASE-5: `needs: gate` makes the build wait for the gate; without it the build runs even when the gate refuses.
- RELEASE-6: The build checks out the dispatch's own commit (no `ref:` or `repository:`)
- RELEASE-7: and its next step refuses unless
  the checkout's head is `needs.gate.outputs.commit`, the commit the gate judged. The gate refuses unless main's head is
  still that commit, so the two are equal; a `ref:` naming the output raises CodeQL's
  `actions/cache-poisoning/poisonable-step` alert at the checkout, so no caller writes one.
- RELEASE-8: `needs: [gate, build]` makes the tag wait for both;
- RELEASE-9: `contents: write` is granted to the tag job only, and no job
  of the release path holds a write grant apart from the tag job, the optional publish job and an app's image jobs.
- RELEASE-10: Nothing dispatches the tag job apart from the release run.
- The optional `publish` job deploys a page and holds nothing else:
  - RELEASE-22: `needs: [gate, build, tag]` makes it run after the tag;
  - RELEASE-23: it holds only `pages: write` and `id-token: write`, in the `github-pages` environment;
  - RELEASE-24: it has no checkout and no secret and runs only `actions/deploy-pages`, pinned by commit with its tag in a comment, so it runs no
    caller code. It deploys the artifact the build uploaded with `actions/upload-pages-artifact`, which lies
    outside the release files (TAG-4 never sees it), so a broken page generator stops the release before the tag.
  - RELEASE-25: A failed
    publish leaves the release standing; "re-run failed jobs" deploys the page again within the Pages artifact's retention (one
    day by default, `retention-days` of the upload sets it). The precedent for the permission
    pair is quality's `report.yml` publish job.
- `timeout-minutes` on `publish` is a convention that `report.yml` shares; `job-timeout` judges only workflows that run on
  `pull_request` or `push` (TIMEOUT-2), so it does not judge this `workflow_dispatch` workflow.
- GATE-2: `commit` is the gate's output, the commit it judged.
- TAG-3: `artifact` names an artifact of the same run.
- TAG-4: Its regular top-level files, dotfiles included, are the release's files and
  nothing else is uploaded; a file name carries no `#`, which gh reads as the start of an asset label. A build that makes
  release files turns its caches off.
- TAG-5: Omit it for a release without files.
- TAG-6: `notes` is optional; GitHub's generated notes follow it. A build that writes notes computes each hash from the very
  file it uploads.
- TAG-7: The generated notes start at the highest earlier tag of the version's own form (three numbers or
  four), so a repository with two forms gets no notes against the other form's release, and at GitHub's default when
  none exists. A head may edit the notes after publishing: an immutable release still accepts edited notes.
- TAG-15: `release-tag.yml` grants nothing at workflow level and only `contents: write` to its job,
- TAG-16: passes its inputs to
  the shell only through `env`,
- TAG-17: and has no step that can be skipped apart from the fetch, which runs only for a
  named artifact.
- GATE-3: `release-gate.yml` has no step that can be skipped,
- GATE-4: passes the version, form, commit and ref to the shell only
  through `env`,
- GATE-5: writes its grammars with no character range, whose meaning would follow the locale,
- GATE-6: and judges the form before any step reads the repository.
- RELEASE-11: `quality`'s own release uses GitHub's same-repository form, `uses: $/.github/workflows/release-gate.yml` and
  `release-tag.yml`, so it calls the workflows of its own commit. Every other repository pins the commit sha of a quality tag and names the tag in a comment.
- RELEASE-12: quality's build also tags the gated commit in its own checkout (the tag never leaves the runner) and runs
  `scripts/verify_release.py`,
- RELEASE-13: which proves one release tag is at the commit,
- RELEASE-14: the installed
  version equals it,
- RELEASE-15: the gate's tool refs pin it
- RELEASE-16: and every rule's release facts name a
  release.
- RELEASE-17: quality's `release.yml` has no job or step that can be skipped,
- RELEASE-18: its checkout persists no credentials,
- RELEASE-19: and its build sets up a Python that `pyproject.toml`'s `requires-python` admits.

## The version

- GATE-7: A library or tool releases `X.Y.Z`, each part a number without leading zeros, tagged `vX.Y.Z`.
- GATE-8: An app releases
  `YYYY.MM.DD.N`, tagged `vYYYY.MM.DD.N`: a four-digit year, month `01` to `12`, day `01` to `31`, and `N` counting the day's
  releases from 1 without leading zeros.
- GATE-9: An app's call of the gate passes `form: YYYY.MM.DD.N` beside the version; without
  `form` the gate judges `X.Y.Z`.
- GATE-10: The gate counts only tags of the caller's form as release tags, so a tag of the other form
  on the commit never refuses it.

The image takes the version as a Docker tag.

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

- IMAGE-1: The caller job grants all four permissions, even with `push: false`; GitHub refuses a call that grants less.
- IMAGE-2: The image is `ghcr.io/<owner>/<repository>`, lowercased; `image` adds a sub-name for a repository with several images
  (`worker` gives `ghcr.io/<owner>/<repository>/worker`).
- IMAGE-3: An `image` that is not lowercase path
  components is refused before anything is built: `Refused: the image <image> is not lowercase path components.`
- IMAGE-4: `version` is the image tag as given; only a valid Docker tag passes, else `Refused: the version <version> is not a
  valid Docker tag.`
- IMAGE-15: `image`, `version` and `build-args` reach a step only through the job's `env`, never inside a `run` script.
- The tag job names each image job in its `needs` (for example `needs: [gate, build, image]`), so the tag and the
  release follow only pushed images.
- IMAGE-5: `context` (default `.`), `file` (default the context's `Dockerfile`) and `target` (default the last stage) select the
  build.
- IMAGE-6: `build-args` holds `NAME=value` lines. The provenance records them, so a secret goes into `build-secrets` as
  `id=value` lines and is read with `RUN --mount=type=secret,id=<id>`. Pass `build-secrets` by name; never
  `secrets: inherit`.
- IMAGE-19: Every build gets `SERVICE_VERSION=<version>` as a build argument, so the release version has one owner and no app
  repeats it in its `release.yml`. image.yml writes it after the caller's `build-args` lines, so it is the last value a
  builder reads. An app that wants it in the running image declares `ARG SERVICE_VERSION` and
  `ENV SERVICE_VERSION=$SERVICE_VERSION` in its final stage; an image that declares no such argument builds unchanged.
  A `build-args` line named `SERVICE_VERSION`, double-quoted or not, is refused before anything is built:
  `Refused: build-args sets SERVICE_VERSION, which image.yml sets from version.`
- IMAGE-7: The image is built for `linux/amd64` on a GitHub-hosted runner,
- IMAGE-8: from the dispatched commit, the one the gate judged.
- IMAGE-18: `runner` (default `ubuntu-latest`) names the GitHub-hosted runner the image job runs on: a larger one for an image a
  standard runner's disk cannot hold.
- IMAGE-9: BuildKit adds its provenance (`mode=max`) and an SBOM to the image;
- IMAGE-10: with `push`, `actions/attest` adds a signed
  SLSA build provenance, signed by `image.yml`. A consumer verifies it with `gh attestation verify oci://<ref> --repo
  <owner>/<repository> --signer-workflow overnightworks/quality/.github/workflows/image.yml --source-ref refs/heads/main
  --predicate-type https://slsa.dev/provenance/v1`.
- IMAGE-11: `cache: true` reads and writes the GitHub Actions build cache; it stays off for a release.
- IMAGE-12: `submodules: true` checks out the commit's git submodules, one level. The job's token fetches them from github.com and
  is not persisted after the checkout, so a submodule that token cannot read fails there. The commit pins
  each one.
- IMAGE-13: The outputs, set only with `push`: `digest` (`sha256:<hex>`) and `ref`
  (`ghcr.io/<owner>/<repository>[/<image>]:<version>@<digest>`).
- IMAGE-16: `reproducible-layers: true` (default `false`) gives every file in the layers one fixed time, so a layer whose inputs
  did not change keeps its digest from one release to the next, and a node that rolls a release pulls only the changed
  layers. The build then sets `SOURCE_DATE_EPOCH` to the constant `0` and the image output to
  `type=image,rewrite-timestamp=true`;
- IMAGE-17: with `false` the build is unchanged. Four points:
  - The epoch is a constant, never the commit's time, because a layer repeats only when its files' times do. It reaches
    the build as a build argument, which the provenance records like every other one. A `SOURCE_DATE_EPOCH` in the
    caller's `build-args` wins over it, and a Dockerfile that declares `ARG SOURCE_DATE_EPOCH` reads `0`.
  - The workflow fixes times, not bytes: a layer repeats only if the Dockerfile writes no logs, caches or wall-clock
    data into it.
  - The image's `created`, the history entries of the build and the index annotation
    `org.opencontainers.image.created` read `1970-01-01`, so a filter or a sort by image age treats the image as old.
  - BuildKit is not pinned: a newer BuildKit can assemble a layer differently once, and its digest then changes.
- IMAGE-14: The package takes the access of the caller's repository; a deployment that pulls it needs read access set
  on the package.

## Refusals

- GATE-11: Each refusal of the gate prints one line on stderr and fails the gate; no tag exists yet.

| ID | Line | What to do |
|---|---|---|
| GATE-12 | `Refused: dispatched from <ref>, a release is dispatched from main.` | Dispatch with `--ref main` (the default). |
| GATE-13 | `Refused: the version is not X.Y.Z, each part a number without leading zeros.` | Give `1.2.3`, not `v1.2.3`, `1.2` or `01.2.3`; an app's call passes `form: YYYY.MM.DD.N`. |
| GATE-14 | `Refused: the form <form> is neither X.Y.Z nor YYYY.MM.DD.N.` | Fix the `form` the caller's `release.yml` passes. |
| GATE-15 | `Refused: the version is not YYYY.MM.DD.N, month 01 to 12, day 01 to 31, N a number from 1 without leading zeros.` | Give `2026.11.06.1`, not `v2026.11.06.1`, `2026.11.6.1`, `2026.13.06.1` or `2026.11.06.0`. |
| GATE-16 | `Refused: main moved from <sha> to <sha> after the dispatch.` | Dispatch again; the release is of main's head at the dispatch. |
| GATE-17 | `Refused: the tag v<version> exists.` | Choose the next version; a tag is never moved. |
| GATE-18 | `Refused: <sha> already carries the release tag <tag>.` | The commit is released; release a later commit. |
| GATE-19 | `Refused: CI has no push run on <sha>.` | Wait for CI's push run on main, or find why none ran. |
| GATE-20 | `Refused: CI's push run <id> is <status>.` | Wait until it completes. |
| GATE-21 | `Refused: CI's push run <id> concluded <conclusion>.` | Fix main; release the next green commit. |
| GATE-22 | `Refused: the quality / verdict of CI's push run <id> is <conclusion>.` | The gate's verdict job did not succeed; fix it on main. |
| GATE-23 | `Refused: no single pull request merged <sha> into main.` | Every main commit comes from exactly one merged pull request; release a commit that does. |
| GATE-24 | `Refused: pull request #<n> merged with quality / verdict <conclusion>; release the next commit merged with a green verdict.` | The commit was merged past a red or missing verdict (an emergency merge). Only that commit is refused; its content ships with the next commit merged green. |

- GATE-25: A later re-run does not lift this: only check runs finished by the merge count.

- GATE-26: The check name `quality / verdict` and the CI file name `ci.yml` are fleet contracts: the caller job of
  [`gate.yml`](gate.md) is named `quality`.

- GATE-27: Of several runs, the one with the highest id decides, for CI's push run and for the merged pull request's verdict check run
  alike.

- TAG-8: These refusals stop the attempt before publishing; none creates or changes a tag or release:

| ID | Line | What to do |
|---|---|---|
| TAG-9 | `Refused: files were fetched, but no artifact was named.` | Name the artifact, or fetch nothing. |
| TAG-10 | `Refused: the artifact <name> brought no file.` | The build uploaded nothing under that name; fix the build. |
| TAG-11 | `Refused: the artifact <name> holds <entry>, which is not a regular top-level file.` | Upload regular top-level files only. |
| TAG-12 | `Refused: the file <name> has a # in its name, which gh reads as the start of an asset label.` | Rename the file in the build. |
| TAG-13 | `Refused: the tag v<version> exists at <sha>, not at the gated commit <sha>.` | Choose the next version; a tag is never moved. |

- TAG-14: Re-run the failed jobs after a failed publish step: the tag job runs whole again, keeps the tag at the commit and completes
  the release. After a failed `publish` job, "re-run failed jobs" runs that job alone, within the Pages artifact's
  retention (one day by default); after that the build must run again under a new version. "Re-run all jobs" runs the gate
  again, which refuses the existing tag. A tag left without a release by a defective publish step is completed by hand with
  `gh release create v<version> <files> --verify-tag`.

## Concurrency

- RELEASE-20: One group, `cancel-in-progress: false`: a release never cancels a running release. GitHub keeps at most one pending run
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

## Depending on a fleet package

Ruled by the operator: a library declares another fleet package (an `overnightworks-*` package) by name and version range
in `[project].dependencies`, and its git URL only in its dev group's `[tool.uv.sources]`. A host pins every fleet package
its `uv.lock` resolves, direct or transitive, by git tag (`name @ git+https://github.com/overnightworks/<repository>@vX.Y.Z`),
then runs `uv lock`; the host's tag decides the one version, and a tag outside a library's range fails the lock loudly. A
host that is itself installed without a lock (today only quality, whose machine runs prepare a pinned release with
`uv tool run --offline`) pins the tag's full 40-character commit instead, the tag named in a comment beside it, because
that offline run fails on a git tag even with a warm cache. One exception is ruled: hal keeps appkit's release wheel with
its content hash. Never from an index: the bare names are not the fleet's, so an unpinned
host would install a stranger's package. `fleet-source` ([rules](rules.md)) refuses that in every tracked `uv.lock`; the
library's declaration form is left to review.

Covered are every fleet repository that depends on a fleet package, library or host. The trigger is now for a new
dependency, and an existing pin converts at its repository's next release. A deviation is asked as marketplace
`docs/standards.md` says and recorded here, at the owner, overnightworks/quality.

## quality's own release

- RELEASE-21: A dispatch of quality's `release.yml` gates, builds, tags and publishes a release without files, with GitHub's generated
  notes. An agent dispatches it once the release pull request's review and CI are green on `main`:

```sh
gh workflow run release.yml -R overnightworks/quality -f version=X.Y.Z
```

## The standard

Ruled by the operator: every project is released on one checked path, and every release starts with the same command and only the version: gate,
then the project's own build, then tag; the gate exists once, in quality. Covered are every
repository that publishes a release and every app that ships an image through the platform; which ones do is read from
the callers of `release-gate.yml` (`gh search code release-gate.yml --owner overnightworks`), never kept as a list here. The trigger is a repository's first release after the quality tag that ships the workflows it needs.

A deviation is asked as marketplace `docs/standards.md` says: the covered head names the enduring harm with its
uncertainty, quality's head reviews it, and the operator rules it; the ruling is recorded at the owner, overnightworks/quality.
