"""The name a person reads. The identifier a machine reads is deliberately not here.

Contract: this module owns the plugin's **display name** and nothing else — the
name a human is shown and recognises the plugin by. A toast's sender, a token
label on the user's own RomM server, the headline of a file they open by hand.
Never a path component, a header value, a storage key or a folder.

The identifier — ``romm-tender`` — is kept out of here, and just as deliberately
kept apart across the six questions below rather than folded into one, because
those answers have to stay free to disagree. Within the scope stated after the
list, each bullet names every place its own answer is written — four of the six
are written more than once — so there is no total at the top: a number there
would rot, and a count is checkable only at the bullet that makes it.

- ``domain.user_data_location.APP_DIR_NAME`` says where the user's own data
  lives, and ``SOURCE_FOLDER_NAMES`` beside it spells the same answer again.
  That answer may never follow a manifest: were it read from ``package.json``,
  editing one line of that file would move every user's library on the next
  start, with nothing failing and nothing said.
- ``package.json``'s ``name`` reaches the recovery root and the outgoing
  User-Agent through bootstrap, and those two SHOULD follow the package — a
  recovery folder is named after whatever wrote it, and a server reading a
  User-Agent is being told which package is calling.
- ``.github/workflows/release.yml``'s ``BUILD_ROOT`` names the folder a release
  unpacks into, which Decky then derives its settings, data, log and plugin
  directories from. It was inherited from the CI checkout until that workflow
  pinned it, and inheriting it is what moved every user's data at 0.31.0. The
  same workflow spells it twice more, in the smoke assertion that reads the
  built zip's top-level directory back and again in that assertion's failure
  message — deliberately for the first, because a check that composed its
  expectation from the value it is checking would pass on any value.
- ``services.legacy_install._LEGACY_PLUGIN_FOLDER`` is the folder releases up to
  0.30.1 unpacked into, and ``SOURCE_FOLDER_NAMES`` spells that one again too.
  It is finished history and follows nothing at all.
- ``src/utils/sessionManager.ts``'s ``SESSION_BREADCRUMB_KEY`` names the
  ``localStorage`` row that carries the open play sessions across a plugin
  reload. It is a key over persisted state, so it follows nothing either: a
  rename is a migration nothing can perform, and every row written under the
  old key is simply orphaned.
- ``domain.update_release.DOWNLOAD_URL``,
  ``adapters.github_releases._LATEST_RELEASE_URL`` and the issues address in
  ``src/components/library/PlatformDetail.tsx`` all name the GitHub repository
  the releases are published from — the question spelled the most times. The
  first is the address a user is shown, the second the API route the update
  check asks, the third shipped screen text; they differ in host and in path
  shape, so none composes from another without inventing a rule. Nothing checks
  that the three agree, and GitHub makes that drift quieter rather than louder:
  a renamed repository keeps answering its old address by permanent redirect —
  measured on this repo's own rename, ``/repos/danielcopper/decky-romm-sync``
  answers 301 to ``/repositories/<numeric id>``, and ``urlopen``'s default
  opener follows a 301 on GET — so a literal left behind goes on working and
  nothing ever surfaces the disagreement. What does break the check is the old
  address no longer answering for THIS repository: deleted, made private, or the
  freed name taken by another repository. The last is the one worth stating, and
  it is not a stranger: all three literals pin the owner, so the only account
  that can take the freed name is the same one, and the check would then read
  ANOTHER OF THIS OWNER'S repositories as if it published Tender. It would
  announce whatever that repository's latest tag parses as — the card needs only
  a version newer than the running one — while an install additionally needs an
  asset named exactly ``Tender.zip`` over there, so the likeliest shape is a card
  offering an update that cannot be installed.

``SOURCE_FOLDER_NAMES`` is why the first and the fourth are each spelled twice:
it is the list a start-up migration searches, written out rather than composed
from either constant — so the search keeps looking where the data actually is
once one of them moves.

**Scope of the bullets**: the plugin's own shipped code, its manifests, and the
workflow that builds a release. Outside those the identifier is written in dozens
of places — documentation prose, ADRs, tests, dev scripts, repository metadata,
badge URLs — and none of them is enumerated here, because that list could not be
kept true and nothing would notice it going stale. Read a bullet as "every place
this answer is written in what ships or builds", never as "everywhere the string
occurs".

Three sites are called out anyway, because each looks like an answer and is not.
``mise.toml``'s deploy destination and the log path in
``.github/ISSUE_TEMPLATE/bug_report.yml`` sit outside the scope above and both
follow the THIRD answer — the delivered folder — one to install into it, the
other to tell a reporter where Decky's logs ended up. ``LibraryPage.tsx`` is
inside it and still answers nothing: it uses the identifier as a log-line prefix
that nothing reads back, a diagnostic tag, where the name a reader is shown is
this module's ``DISPLAY_NAME``.

Fold any two together and one question's answer starts deciding another's, in
whichever direction the fold happened to point — and each of those failures is
silent: a library the plugin cannot find, a recovery folder that no longer
matches the package that wrote it, every Decky-side directory moved by an edit
that was about something else, a warning card that simply stops firing, a
running game whose session is forgotten at the next reload, an update check
pointed at a repository this plugin is not released from.
"""

from __future__ import annotations

# Must match ``plugin.json``'s ``name`` and the frontend's ``PLUGIN_NAME``
# (``src/utils/toast.ts``, which is what ``definePlugin`` hands back) — Decky
# reads the first for its plugin list and the second for the QAM header. Nothing
# checks that the three agree.
DISPLAY_NAME = "Tender"
