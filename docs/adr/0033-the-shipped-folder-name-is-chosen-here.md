# The folder the plugin ships as is chosen here, and it is `romm-tender`

## Status

Accepted. **Closes the exposure [ADR-0031](0031-user-data-lives-outside-the-plugin-directory.md) described but could not
remove** — that the name Decky knows this plugin by "is decided by a string this project does not control, that a
routine repository operation can change, and whose change is silent and irreversible from inside". ADR-0031 took the
user's data out of that string's blast radius; this takes the string itself out of the repository name's hands.

## Context

Decky derives all four of its per-plugin directories — `plugins/`, `data/`, `settings/` and `logs/` — from one folder
name (`decky_loader/plugin/sandboxed_plugin.py`), and that folder is whatever the release zip's top-level directory is
called.

The Decky CLI offers exactly two sources for that name, and no third:

- `FilenameSource::PluginName` — `plugin.json`'s `name`, which here is `Tender`.
- `FilenameSource::Directory` — the basename of the directory it is told to build from.

One value becomes both the zip's filename and the prefix of every entry inside it, so the source chosen decides the
installed folder (`src/cli/plugin/build.rs`, `zip_plugin`). Our release workflow passes `-s directory` and built from
`$GITHUB_WORKSPACE`, which GitHub names after the repository — so the delivered folder followed the repository name, and
nothing in this repository recorded that it did.

**That is not a hypothetical.** Renaming the repository to `romm-tender` in August moved every user's Decky-side
directories at 0.31.0, with no commit, no PR and nothing in the plugin asking for it. ADR-0031 and
[ADR-0032](0032-shortcuts-are-rewritten-in-place.md) removed the two consequences that hurt most — the user's library
and settings now live under their own home, and the launcher every Steam shortcut runs lives beside them.

What remains in the blast radius is smaller but not nothing, and it is the part neither of those ADRs could reach: **the
folder name is the identity Decky matches on.** `PluginBrowser.find_plugin_folder` compares the name in each installed
`plugin.json` to decide whether an install replaces an existing one, and the folder is what it removes. A rename that
Decky does not recognise leaves the previous install standing beside the new one — which is exactly the state the
warning card from #1865 exists to explain, and which a user cannot resolve without being told which of two
identical-looking plugins to delete.

So the remaining question is not "where does the data live" but "who decides the name". Today: whoever renames the
repository, silently.

## Decision

**1. The delivered folder name is `romm-tender`, and it is chosen in this repository.** The release workflow copies the
checkout to a directory of that name under `$RUNNER_TEMP` and builds from there, so `FilenameSource::Directory` reads a
name this repository wrote rather than one GitHub supplied.

**2. The name is the one already in the field.** 0.31.0 and 0.32.0 shipped `romm-tender`, so pinning it moves nothing
for anyone. This decision changes who owns the name, not the name.

**3. The packaging smoke test asserts the zip's top-level directory**, in the same workflow step that already checks the
zip's contents, and before the upload. That assertion is the only thing standing between a wrong pin and a wrong
release: the release workflow has no `pull_request` trigger, so no pull request ever exercises it. A wrong pin therefore
fails the release build loudly instead of shipping a folder that moves everyone's directories again.

**4. The name is a home of the identifier, and never derived from another one.** It answers its own question — what
folder does a release unpack into — and must stay free to disagree with where the user's data lives
([CONTEXT.md → Display name vs identifier](../../CONTEXT.md)). Deriving either from the other reintroduces exactly the
coupling this ADR removes, in whichever direction the derivation points.

## Consequences

- **A repository rename no longer touches users.** The name can now only change by editing the line that holds it, in a
  diff that says so.
- **Changing it is now a deliberate act with a stated cost.** It moves Decky's four directories, and — because Decky
  matches installs by `plugin.json`'s name rather than by the folder — leaves the previous install standing. That cost
  was always there; what is new is that it is paid on purpose.
- **`mise.toml`'s development deploy target is a hand-kept copy that must agree**, in five places, and nothing checks
  it. A disagreement is visible immediately (the plugin deploys beside itself rather than over itself) rather than
  silently, which is why it is left as a copy rather than made derived.
- **The release asset name is unaffected.** `Tender.zip` is tied to `plugin.json`'s `name` through Decky's
  install-from-URL lookup, not to the folder — the workflow's own comment records that chain, and this decision does not
  touch it.
- **The CLI's `-s directory` flag now carries meaning it did not have before.** It used to be the reason the name was
  out of our hands; it is now the mechanism by which we hold it. A future reader tidying the build step towards the
  CLI's default would undo this decision without noticing, which is why the alternative below is recorded rather than
  merely rejected.

## Alternatives considered

**Use the CLI's other source, `-s plugin-name`.** The folder would become `Tender`, the zip would be named `Tender.zip`
by construction, and one name — `plugin.json`'s — would then decide the asset name, the folder and the identity Decky
matches on, letting the workflow's hard-coded asset name and its copy step go away. Genuinely attractive, and rejected
on cost: it renames the installed folder a second time for everyone already on 0.31.0 or later, moving Decky's log and
data directories again, and stranding the copies ADR-0031 deliberately left behind under a directory name that would
then match no installed plugin. A second folder move is a poor price for a tidier workflow.

**Check the repository out into a fixed `path:` instead of copying.** Same result at the packaging step, but every other
step in the job would need its working directory moved, including the actions that look for `package.json` at the
workspace root. A larger diff across steps that have nothing to do with this decision, for an identical outcome.

**Leave the folder following the repository name.** The status quo, and the thing that caused 0.31.0. Its only argument
is that the name happens to be right today.

## Related

- [ADR-0031](0031-user-data-lives-outside-the-plugin-directory.md) — the user's data leaves the plugin's directories.
- [ADR-0032](0032-shortcuts-are-rewritten-in-place.md) — the launcher leaves the plugin folder and shortcuts are
  repointed.
- [CONTEXT.md → Display name vs identifier](../../CONTEXT.md) — which of the plugin's two names a new string takes, and
  the homes each has.
