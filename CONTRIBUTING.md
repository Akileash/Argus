# Contributing to Argus

This is how we work so that many people can change the repo at the same time without stepping on each
other. It is short on purpose. If a rule here gets in your way, tell the team; do not work around it.

## Who owns which folder

Edit your own folder freely. For any other folder, ask the owner first, or keep the change very small.

| Folder | What it is | Owner |
|---|---|---|
| `device/hardware/team/<module>/` | one hardware teammate's sensor script, mapping and README | the teammate who wrote that module. **Only they edit it.** |
| `device/hardware/argus/`, `device/hardware/mock/`, `device/hardware/replay/` | our drivers, fake sensors, recording playback | device software |
| `device/argus/`, `device/tests/`, `device/scenarios/` | detectors, events, supervisor, CLI, tests | device software |
| `web/` | the dashboard and the API | web |
| `worker/` | the report worker | web |
| `supabase/` | database migrations, seed data, SQL tests | web |
| `contracts/` and `device/argus/contracts.py` | the shared formats between parts | **everyone** (see "Shared contracts") |
| `docs/`, `.plans/`, `scripts/`, `.github/` | docs and tooling | whoever changes the thing the doc describes |

## Rules that prevent most conflicts

1. **One folder per person.** A hardware teammate changes only `device/hardware/team/<their module>/`
   (their script goes in its `vendor/` subfolder, unchanged). If your pull request touches anything else,
   say why in the description.
2. **Machine-specific settings go in `device/config.local.yaml`.** It is ignored by git, and it is merged
   over `device/config.yaml` when you run on your laptop. Use it for `impl: team`, serial ports and pairing.
   Do not edit `device/config.yaml` to switch your own hardware on: that file is shared.
3. **Do not edit generated files by hand.** `pnpm-lock.yaml` and `device/uv.lock` are generated.
   See "Lockfile conflicts" below.
4. **Do not edit `contracts/` or `device/argus/contracts.py` without telling everyone first.** Every part
   depends on them. Post in the team chat, wait for a yes, and change the matching code and tests in the same
   pull request.
5. **Do not rename or reformat files you do not own.** A big formatting change in someone else's folder is a
   guaranteed conflict for them.
6. **Do not commit secrets or device tokens.** `.env` files and `config.local.yaml` are ignored by git for
   this reason. `argus-cli pair` writes your token to `config.local.yaml`.

## Branches and pull requests

- Branch from `main` and name the branch `<area>/<short-description>`, in lower case with dashes:
  `hw/range-script`, `device/ttc-threshold`, `web/incident-filters`, `docs/handoff-fixes`.
- **Keep pull requests small**: one thing, ideally under about 300 changed lines. Open it early. A small
  pull request is reviewed in minutes; a big one waits for days and then conflicts.
- Pull `main` into your branch often (`git pull origin main`), at least once a day.
- Commit messages: a short prefix and what changed, for example `feat(range): team range script and mapping`
  or `fix(device): reload keeps new options`.
- Before you ask for review, run the one check command below and make sure nothing is red.

```bash
git switch main && git pull
git switch -c hw/range-script
# ... work, commit ...
scripts/check.sh            # must say "OK: nothing failed."
git push -u origin hw/range-script
```

## The one check command

Run this from the repo root before you push or merge:

```bash
scripts/check.sh
```

It runs the device tests and lint, the Node tests and type checks, shellcheck on the shell scripts, and
the SQL tests. A step your machine cannot run (for example no Node installed) is printed as `SKIP` with the
reason, so you can see exactly what was not checked. The same script runs on every pull request.

Hardware teammates who only have Python can run the device part alone:

```bash
scripts/check.sh --device
```

Needs: `uv` (Python), and for the full run also `node` 22 with `pnpm`, `shellcheck`, and PostgreSQL 16.

## How to pull your hardware script in

You wrote and tested a sensor script. This is how it becomes the `team` implementation of a module, without
touching anyone else's files. The full guide with examples is `docs/TEAM-HANDOFF.md`.

```bash
git switch -c hw/range-script
cd device
# 1. copy your script, unchanged, into device/hardware/team/range/vendor/
uv run argus-cli team init range --format text       # 2. starter mapping.yaml (or --format json)
# 3. edit device/hardware/team/range/mapping.yaml: the command, your field names, your units
uv run argus-cli team check range --sample sample.txt   # 4. paste 10-20 lines of real output first
```

Then switch it on for your machine only, in `device/config.local.yaml`:

```yaml
modules:
  range: { impl: team, fallback: mock }
```

On the Pi, run the contract tests (`uv run pytest tests/contract --module range --impl team`), then commit
only `device/hardware/team/range/` and open a pull request. Send the integrator the hand-off message from
the guide, including your `sample.txt`.

## Lockfile conflicts

`pnpm-lock.yaml` and `device/uv.lock` are generated from `package.json` and `device/pyproject.toml`. When two
branches both change them, git reports a conflict inside a file nobody can sensibly merge by hand. The rule:
**take either side, then regenerate.** The script does both:

```bash
git merge origin/main        # CONFLICT (content): Merge conflict in pnpm-lock.yaml
scripts/fix-lockfiles.sh     # takes one side of each lockfile, regenerates it, stages it
git commit                   # (or: git rebase --continue)
```

Needs network access (it asks the package registries); with no network, `LOCK_OFFLINE=1 scripts/fix-lockfiles.sh`
uses the local cache. If the script says it could not regenerate, run the command it prints, then `git add`
the file. Other conflicting files are listed for you to resolve by hand.

To see the whole workflow work (two teammates and a lockfile conflict, in a throwaway clone that does not
touch your branches), run `scripts/merge-dry-run.sh`.

## Shared contracts

`contracts/` holds the JSON schemas and API description that the device, the web app and the worker agree on,
and `device/argus/contracts.py` holds the Python sample types and module interfaces every hardware
implementation must satisfy. A change here breaks other people's work silently. So: tell everyone before you
start, keep the change backward compatible if you can, and update the matching code in the same pull request.

## Is there a remote yet?

Not yet. This repository has no GitHub remote, so the pull-request steps above are for the day it does, and
until then `git merge` between local branches works the same way. The check workflow in
`.github/workflows/check.yml` is ready and needs no secrets.
