"""The name a person reads. The identifier a machine reads is deliberately not here.

Contract: this module owns the plugin's **display name** and nothing else — the
name a human is shown and recognises the plugin by. A toast's sender, a token
label on the user's own RomM server, the headline of a file they open by hand.
Never a path component, a header value, a storage key or a folder.

The identifier — ``romm-tender`` — is kept out of here, and just as deliberately
kept in seven separate places rather than one, because it answers six questions
that have to stay free to disagree. Count them off the list below: one place per
question, except the sixth, which is spelled twice.

- ``domain.user_data_location.APP_DIR_NAME`` says where the user's own data
  lives. That answer may never follow a manifest: were it read from
  ``package.json``, editing one line of that file would move every user's
  library on the next start, with nothing failing and nothing said.
- ``package.json``'s ``name`` reaches the recovery root and the outgoing
  User-Agent through bootstrap, and those two SHOULD follow the package — a
  recovery folder is named after whatever wrote it, and a server reading a
  User-Agent is being told which package is calling.
- ``.github/workflows/release.yml``'s ``BUILD_ROOT`` names the folder a release
  unpacks into, which Decky then derives its settings, data, log and plugin
  directories from. It was inherited from the CI checkout until that workflow
  pinned it, and inheriting it is what moved every user's data at 0.31.0.
- ``services.legacy_install._LEGACY_PLUGIN_FOLDER`` is the folder releases up to
  0.30.1 unpacked into. It is finished history and follows nothing at all.
- ``src/utils/sessionManager.ts``'s ``SESSION_BREADCRUMB_KEY`` names the
  ``localStorage`` row that carries the open play sessions across a plugin
  reload. It is a key over persisted state, so it follows nothing either: a
  rename is a migration nothing can perform, and every row written under the
  old key is simply orphaned.
- ``domain.update_release.DOWNLOAD_URL`` and
  ``adapters.github_releases._LATEST_RELEASE_URL`` both name the GitHub
  repository the releases are published from — the sixth question, and the only
  one held twice. One is the address a user is shown, the other the API route
  the update check asks; they differ in host and in path shape, so neither
  composes from the other without inventing a rule. Nothing checks that the two
  agree, and a repository rename breaks both halves at once into the plugin's
  quietest failure: the check then answers "nothing newer" for good, which is
  exactly what a Deck with no network looks like.

``SOURCE_FOLDER_NAMES``, beside ``APP_DIR_NAME``, spells the first and the
fourth out again as the list a start-up migration searches, rather than
composing them from either — so the search keeps looking where the data actually
is once one of them moves.

The count covers what the running plugin holds, plus the workflow that builds
it. The identifier also sits in repository metadata (the docs site, the analysis
config, the issue templates) and in the dev loop's deploy destination
(``mise.toml``), and those are deliberately not questions of their own: the
first group answers the sixth for tooling that never runs on a user's Deck, and
the last follows the delivered folder the third bullet names.

Fold any two together and one question's answer starts deciding another's, in
whichever direction the fold happened to point — and each of those failures is
silent: a library the plugin cannot find, a recovery folder that no longer
matches the package that wrote it, every Decky-side directory moved by an edit
that was about something else, a warning card that simply stops firing, a
running game whose session is forgotten at the next reload, an update check that
asks a repository nobody publishes to.
"""

from __future__ import annotations

# Must match ``plugin.json``'s ``name`` and the frontend's ``PLUGIN_NAME``
# (``src/utils/toast.ts``, which is what ``definePlugin`` hands back) — Decky
# reads the first for its plugin list and the second for the QAM header. Nothing
# checks that the three agree.
DISPLAY_NAME = "Tender"
