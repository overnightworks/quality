# Naming

Reference for the engineer or head who names a package or renames a repository: which name each ecosystem gets, where the rule bends, and how a Sonar key changes. Nothing in quality checks these names yet; the document is the rule's text, not a gate.

## The stem

Every repository has one stem: its GitHub name, a lowercase word or a hyphenated pair. The name carries no role suffix (`-runtime`, `-service`, `-client`, `-server`, `-lib`, `-sdk`, `-api`); the role lives in the repository's description and in `role`, never in the name. Every other name the repository owns derives from the stem.

## The forms

| Namespace | Form | Why |
|---|---|---|
| Python distribution | `overnightworks-<stem>[-<component>]`; bare `<stem>` only under `[tool.uv] package = false` | the prefixed names are free on PyPI and cannot shadow a taken one; a distribution nothing installs cannot clash |
| Python import | `<stem>[_<component>]` | a valid identifier that names its repository |
| CLI | `<stem>[-<component>]`; features are subcommands | one command per repository; a separately installed process stays visibly part of it |
| npm name of a packed `package.json` | `overnightworks-<stem>[-<component>]`, unscoped | the same prefix as the Python distribution |
| Sonar project key | `overnightworks_<stem>` | SonarCloud's org-key form; its check is SONAR-59 in [sonar.md](sonar.md) |
| Container image path | derived from the repository, see IMAGE-2 in [release.md](release.md) | one owner for the path |

A component may be several words (`speech-service`, `speech-fetch-hearing-model`): the suffix ban is on the repository name, not on a component.

## Names the ecosystem dictates

The ecosystem finds these only in its own form, so they are the only exceptions: a Slidev addon is `slidev-addon-<stem>`, a Claude or Codex plugin is `<stem>`.

## Fixed points

- hopin and songmaker keep their bare distributions `hopin` and `songmaker` (operator, 09.11.2026, marketplace#571, A): a distribution nothing installs keeps the bare stem.
- ACE-Step-1.5 is a fork and keeps its upstream names.

## Import clashes

The distribution prefix does not protect an import name. A clash matters only inside one environment: tools (`aco`, `quality`) run in their own, so only a library that enters foreign environments (`webauth`, `agents`) can clash; `agents`, for instance, is also the import name of OpenAI's Agents SDK. A change that names or renames an import proves it with

```sh
python -I -c "import <name>; print(<name>.__file__)"
```

resolving into the repository's own tree, and with no foreign distribution named like the import in the affected `uv.lock` files.

## P2: change a Sonar key

For a repository with `sonar-project.properties` (and its bootstrap workflow, where one exists). Going back reverses the order of the key and the GitHub name: the key changes back first.

1. An agent opens the pull request that carries the rename's other changes, green under the old name. The key commit stays local (pushed under the old name it fails the gate's preflight) and other merges are held.
2. An agent disables the repository's CI workflow and drains it: it lists every unfinished run (any status but completed) over all pages and waits until each is completed or cancelled, listing again until none is left. This happens before the first identity change in either direction; the workflow is enabled again only once the GitHub name and the Sonar key agree.
3. The operator runs Administration, Update Key at SonarCloud, once the project's Background Tasks show nothing pending or in progress.
4. The operator saves the binding again at SonarCloud › Administration › General Settings › Repository binding.
5. An agent enables the workflow and pushes the key commit onto the pull request; once the verdict is green, it merges.

A repository on automatic analysis only does steps 3 and 4 on the server.

Check: main's analysis lands in the new key, and `api/components/search` lists no project with the old key. A project there means a scanner re-created it.
