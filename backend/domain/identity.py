"""The names this program goes by, and the release it is.

Contract: three values, each answering a different question about what to call
this program.

- :data:`DISPLAY_NAME` is what a HUMAN reads — a toast's sender, a token label
  on the user's own RomM server, the headline of a file they open by hand.
  Never a path component, a header value, a storage key or a folder.
- :data:`PACKAGE_NAME` is what a MACHINE reads about who is calling — the
  outgoing User-Agent, and the name of the recovery root a bundle is written
  into.
- :data:`VERSION` is which RELEASE this is. Three consumers today, and the list
  is meant as one: the other half of that User-Agent, the ``plugin_version``
  recorded in a recovery bundle's manifest, and the ``client_version`` a
  registered device carries on the user's own RomM server
  (``services/saves/service.py`` hands it to ``DeviceRegistry``). The first two
  are reached from ``bootstrap/``; the third is not, which is why it is easy to
  miss when reading the composition root alone.

The identifier — ``romm-tender`` — is kept in three separate places rather than
one, because it answers three questions that have to stay free to disagree:

- ``domain.user_data_location.APP_DIR_NAME`` says where the user's own data
  lives. That answer may never follow a manifest, and now may just as little
  follow :data:`PACKAGE_NAME`: were it read from either, editing one line would
  move every user's library on the next start, with nothing failing and nothing
  said.
- :data:`PACKAGE_NAME` reaches the recovery root and the outgoing User-Agent
  through bootstrap, and those two SHOULD follow the package — a recovery
  folder is named after whatever wrote it, and a server reading a User-Agent is
  being told which package is calling.
- ``frontend/src/utils/sessionManager.ts``'s ``SESSION_BREADCRUMB_KEY`` names
  the ``localStorage`` row that carries the open play sessions across a reload.
  It is a key over persisted state, so it follows nothing either: a rename is a
  migration nothing can perform, and every row written under the old key is
  simply orphaned.

Fold any two together and one question's answer starts deciding another's, in
whichever direction the fold happened to point — and each of those failures is
silent: a library the program cannot find, a recovery folder that no longer
matches the package that wrote it, a running game whose session is forgotten at
the next reload.

**Three constants in one module is not a fold.** They are three different
values, so no edit to one can reproduce another by accident. The identically
SPELT pair is :data:`PACKAGE_NAME` against ``APP_DIR_NAME``, and that pair is
exactly the one still standing in two modules — which is the whole of the rule
above. What this module replaced was a manifest read, not a separation:
``package.json`` used to supply the name and the version at boot, so a file the
frontend's package manager owns decided what a server was told about the
backend.

**:data:`VERSION`, not ``__version__``.** In Python ``__version__`` means "the
version of this module or package". ``backend/`` is not a package — there is no
``backend/__init__.py``, and the importable packages are ``domain``,
``services``, ``adapters`` and so on. ``domain.identity.__version__`` would
therefore read as the version of the identity module; what is meant is the
version of the program.

Two questions have gone rather than moved, and both for the same reason. The
folder a release unpacked into was Decky's, asked because Decky DERIVED its
settings, data, log and plugin directories from it (ADR-0035). The folder
earlier releases unpacked into was the other half of the same story — what a
start-up migration searched, which is a search nothing performs any more
(ADR-0036). Hosting the backend ourselves derives nothing: the directories come
from the environment, so neither question has an asker left.
"""

from __future__ import annotations

# Must match the frontend's ``PLUGIN_NAME`` (``frontend/src/utils/toast.ts``),
# which is the name the QAM header carries. Nothing checks that the two agree.
DISPLAY_NAME = "Tender"

# The identifier a machine reads. Spelt identically to
# ``domain.user_data_location.APP_DIR_NAME`` and deliberately not shared with
# it — see the module docstring for which question each one answers.
PACKAGE_NAME = "romm-tender"

# Stamped by release-please on every release (``release-please-config.json``
# carries a ``generic`` extra-file entry pointing at this module, and the
# marker below is how it finds the line). Never edit it by hand: a version
# written here out of band is one release-please will overwrite without
# noticing, and until it does, every consumer listed in the module docstring
# claims a release that was never cut.
VERSION = "0.33.0"  # x-release-please-version
