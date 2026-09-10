"""The name a person reads. The identifier a machine reads is deliberately not here.

Contract: this module owns the plugin's **display name** and nothing else — the
name a human is shown and recognises the plugin by. A toast's sender, a token
label on the user's own RomM server, the headline of a file they open by hand.
Never a path component, a header value, a storage key or a folder.

The identifier — ``romm-tender`` — is kept out of here, and just as deliberately
kept in four separate places rather than one, because it answers four questions
that have to stay free to disagree:

- ``domain.user_data_location.APP_DIR_NAME`` says where the user's own data
  lives. That answer may never follow a manifest: were it read from
  ``package.json``, editing one line of that file would move every user's
  library on the next start, with nothing failing and nothing said.
- ``package.json``'s ``name`` reaches the recovery root and the outgoing
  User-Agent through bootstrap, and those two SHOULD follow the package — a
  recovery folder is named after whatever wrote it, and a server reading a
  User-Agent is being told which package is calling.
- ``services.legacy_install._LEGACY_PLUGIN_FOLDER`` is the folder releases up to
  0.30.1 unpacked into. It is finished history and follows nothing at all.
- ``src/utils/sessionManager.ts``'s ``SESSION_BREADCRUMB_KEY`` names the
  ``localStorage`` row that carries the open play sessions across a plugin
  reload. It is a key over persisted state, so it follows nothing either: a
  rename is a migration nothing can perform, and every row written under the
  old key is simply orphaned.

``SOURCE_FOLDER_NAMES``, beside ``APP_DIR_NAME``, spells the first and third out
again as the list a start-up migration searches, rather than composing them from
either — so the search keeps looking where the data actually is once one of them
moves.

Fold any two together and one question's answer starts deciding another's, in
whichever direction the fold happened to point — and each of those failures is
silent: a library the plugin cannot find, a recovery folder under a name it
never shipped as, a warning card that simply stops firing, a running game whose
session is forgotten at the next reload.
"""

from __future__ import annotations

# Must match ``plugin.json``'s ``name`` and the frontend's ``PLUGIN_NAME``
# (``src/utils/toast.ts``, which is what ``definePlugin`` hands back) — Decky
# reads the first for its plugin list and the second for the QAM header. Nothing
# checks that the three agree.
DISPLAY_NAME = "Tender"
