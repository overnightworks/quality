# Naming

Reference for the engineer or head who names a package or renames a repository: which name each ecosystem gets, where the rule bends, and how a Sonar key changes. quality judges the rule on every pull request: the `naming` rule in [rules.md](rules.md) checks the names a repository writes itself, and SONAR-59 the Sonar key.

## The stem

Every repository has one stem: its GitHub name, a lowercase word or a hyphenated pair. The check reads it from the remote `origin`, so a rename ends with `origin` pointing at the new name. The name carries no role suffix (`-runtime`, `-service`, `-client`, `-server`, `-lib`, `-sdk`, `-api`); the role lives in the repository's description, never in the name. Every other name the repository owns derives from the stem.

## The forms

| Namespace | Form | Why |
|---|---|---|
| Python distribution | `overnightworks-<stem>[-<component>]`; bare `<stem>` or `<stem>-<component>` only for a distribution nothing installs (`[tool.uv] package = false`, or one run only from its own checkout) | the prefixed names are free on PyPI and cannot shadow a taken one; a distribution nothing installs cannot clash |
| Python import | `<stem>[_<component>]`, each `-` of the stem or component written `_` (`gmail_cleanup`, `speech_fetch_hearing_model`) | a valid identifier that names its repository |
| CLI | `<stem>[-<component>]`; features are subcommands | one command per repository; a separately installed process stays visibly part of it |
| npm name of a packed `package.json` | `overnightworks-<stem>[-<component>]`, unscoped | the same prefix as the Python distribution |
| Sonar project key | `overnightworks_<stem>` | SonarCloud's org-key form; its check is SONAR-59 in [sonar.md](sonar.md) |
| Container image path | derived from the repository, see IMAGE-2 in [release.md](release.md) | one owner for the path |

A component may be several words (`speech-service`, `speech-fetch-hearing-model`): the suffix ban is on the repository name, not on a component. The check reads a distribution as installed unless `[tool.uv] package = false` says otherwise, judges every tracked `package.json` whether private or not, and accepts `<stem>[-<component>]` there beside the prefixed form; a distribution run only from its own checkout that does not say `package = false` is a deviation.

## Names the ecosystem dictates

The ecosystem finds these only in its own form, so they are the only exceptions: a Slidev addon is `slidev-addon-<stem>`, a Claude or Codex plugin is `<stem>`.

## Fixed points

The names the operator ruled stay as they are: the entries of `src/quality/naming_deviations.toml` (repository, kind, name, the item holding the ruling), which quality's check reads. No repository declares a deviation, and a new one takes effect with a quality release.

| Repository | Kind | Name | Ruling |
|---|---|---|---|
| `overnightworks/hopin` | distribution | `hopin` | edge#24 |
| `overnightworks/songmaker` | distribution | `songmaker` | edge#24 |

ACE-Step-1.5 is upstream and keeps its upstream names; it is a fork, outside the check's coverage.

## Import clashes

The distribution prefix does not protect an import name. A clash matters only inside one environment: tools (`aco`, `quality`) run in their own, so only a library that enters foreign environments (`webauth`, `agents`) can clash; `agents`, for instance, is also the import name of OpenAI's Agents SDK. A change that names or renames an import proves it with

```sh
python -I -c "import <name>; print(<name>.__file__)"
```

resolving into the repository's own tree, and with no foreign distribution named like the import in the affected `uv.lock` files.

## P2: change a Sonar key

For a repository with `sonar-project.properties` (and its bootstrap workflow, where one exists). Going back reverses the order of the key and the GitHub name: the key changes back first.

1. An agent opens the pull request that carries the rename's other changes, green under the old name. The rename of the GitHub repository itself, with `origin` and the clones, is P1, the `aco` skill's "Rename a repository" section of atelier. The key commit stays local (pushed under the old name it fails the gate's preflight) and other merges are held.
2. An agent disables the repository's CI workflow and drains it: it lists every unfinished run (any status but completed) over all pages and waits until each is completed or cancelled, listing again until none is left. This happens before the first identity change in either direction; the workflow is enabled again only once the GitHub name and the Sonar key agree.
3. The operator runs Administration, Update Key at SonarCloud, once the project's Background Tasks show nothing pending or in progress.
4. The operator saves the binding again at SonarCloud › Administration › General Settings › Repository binding.
5. An agent enables the workflow and pushes the key commit onto the pull request; once the verdict is green, it merges.

A repository on automatic analysis only does steps 3 and 4 on the server.

Check: main's analysis lands in the new key, and `api/components/search` lists no project with the old key. A project there means a scanner re-created it.
