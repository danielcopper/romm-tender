# BIOS and Emulator Core Management

Some emulated systems require BIOS files to run games. Without the correct BIOS files, games for those systems will fail
to launch. Tender can download BIOS files directly from your RomM server.

Which BIOS files a system needs depends on the **emulator core** in use — some cores need BIOS, some don't. Because the
two concerns are related but independent, Tender presents the **active core** and its **BIOS state** together in one
place: the **Platforms** tab of the **Library** page, where picking a platform on the left shows everything about it on
the right. Core selection and BIOS file management can each be used on their own — they share a pane only because the
active core determines which BIOS files matter.

## What Are BIOS Files?

BIOS (Basic Input/Output System) files are firmware dumps from original hardware. Emulators need them to accurately
simulate the console's boot process. Common examples:

- **PlayStation** — `scph5501.bin` (and other regional variants)
- **Dreamcast** — `dc_boot.bin`, `dc_flash.bin`
- **Saturn** — `sega_101.bin`, `mpr-17933.bin`

Not all systems need BIOS files. Cartridge-based systems like Game Boy, SNES, and Genesis typically work without them.

## BIOS Status on the Game Detail Page

When you open a game whose platform has BIOS files — on your RomM server, or asked for by the emulator that will run it
— the game detail panel's **BIOS** tab shows the readiness line. Its dot colour is the verdict of the resolver Tender
asks — whether the emulator you launch with has everything it needs — and the same colour is used everywhere in Tender.
No colour is worked out from counting files:

- **Green** — nothing required is missing: "All 3 files melonDS opens by name are in place", or "mGBA marks none of its
  BIOS files as required (3/5 RomM library files)" when the emulator you launch with lists no file it needs, or
  "SwanStation has a BIOS image for every region". An emulator that asks for exactly one file reads "The one file
  DuckStation requires is in place". A line saying the required files are in place can end in "(2 optional missing)":
  files that same emulator lists as optional and that are not in place. Another emulator's optional files are not
  counted there.
- **Amber** — only one thing: the console needs one image per disc region, the images in place serve some regions and
  not others, and nothing else is open — "Beetle PSX has a BIOS image for USA only — Japan and Europe discs will not
  start". Whether a game starts then depends on its disc's region (see
  [When the console needs a BIOS image](#when-the-console-needs-a-bios-image)).
- **Red** — the emulator does not have what it needs: "2 of 3 files melonDS opens by name are in place" (the sentence
  still says how much is there), "The one file DuckStation requires is not in place", a file that is there but is the
  wrong image, or "PlayStation does not start without a BIOS image, and none of the five SwanStation knows is in place"
  where the console itself will not start without one of the images — and, for an emulator that names no image but
  searches your BIOS folder, "PlayStation does not start without a BIOS image — DuckStation (Standalone) found no BIOS
  image in bios/"
- **Grey** — not established, in one of several wordings: "Nothing could be established about what mGBA needs", where
  the emulator could not be asked at all (see [When the requirement is unknown](#when-the-requirement-is-unknown)); "One
  file melonDS opens by name could not be checked" or "Whether the BIOS image Beetle PSX needs is in place could not be
  checked", where the requirement is known and whether you have it could not be settled; "No BIOS is chosen in PCSX2's
  settings, and no game boots until one is", where the emulator's own settings name no BIOS; or a required file the
  resolver would not follow, named with its reason (see
  [When readiness cannot be stated](#when-readiness-cannot-be-stated))

The line also says **how the emulator finds its BIOS**, because that decides what a list of file names means. An
emulator that opens the files it names says so — "2 of 3 files melonDS opens by name are in place". One that searches
your BIOS folder and recognises an image by its contents (where the resolver says it found the image that way) is not
counted at all, because "1 / 1 required" would be true and useless: "DuckStation recognises scph5500.bin in the BIOS
folder" (short form **Found by content**). Where the resolver states that the search reached no image, the line says
what the folder held: "SwanStation found no BIOS image in bios/ for a Japan, USA and Europe console" (**No BIOS image
found**) where it holds no file of a size the emulator accepts — "DuckStation (Standalone) found no BIOS image in bios/"
where the search states no count of files — or "bios/ holds 2 files of a size SwanStation accepts, and it recognises
none of them" (**No image recognised**). Where the console itself does not start without an image (see
[When the console needs a BIOS image](#when-the-console-needs-a-bios-image)), that sentence comes first and this one
follows it as a note: "PlayStation does not start without a BIOS image, and none of the five SwanStation knows is in
place — bios/ holds 2 files of a size SwanStation accepts, and it recognises none of them". Where the search did not
settle, grey, by why — "DuckStation's BIOS folder could not be read completely", or "scph5501.bin could not be read".
Where the resolver states nothing about the search, the line counts the files the emulator **requires** — "The one file
DuckStation requires is not in place" — and never says it opens them by name, because it would also search the folder.
Where nobody has established how an emulator finds its BIOS, the line says what it **lists**, with a note: "All 2 files
Beetle Saturn lists as required are in place. How Beetle Saturn finds its BIOS has not been established", or, red,
"gba_bios.bin, which gpSP lists as required, is not in place" with the same note. A required file that is not in place
beside one that could not be checked names both: "One file melonDS opens by name is not in place, and one could not be
checked", with "1 / 3 required · 1 not checked" as its short form. An emulator the resolver Tender asks does not cover
yet reads grey: "Tender cannot tell what PPSSPP needs yet — the emulator's firmware is not covered by the resolver yet".
The same words — "opens by name", "lists as required" or "requires" — also word a required file nothing could check and
a declaration the resolver would not follow, so one emulator is never described two ways. None of this moves the colour,
which stays the resolver's verdict.

Every one of those sentences is written in one place and both surfaces read it, so the game page and the Library page's
platform pane can never word the same state differently. The pane has a heading to hang a short form on, so it shows
that short form beside **BIOS FILES** — "Nothing required", "Readiness unknown", "1 / 2 required", "1 / 3 regions · USA
only" — with the sentence under it. Both pages put the same library ratio behind the sentence, whichever state it is in,
and neither prints one where your library holds nothing for the platform.

The sentence says what the dot says, and both are about the **required** files. Where the system has none, the dot is
the resolver's verdict, which also weighs whether the console needs an image — so the line says that the emulator you
launch with requires none of the files it names, and the ratio beside it is inventory, counting the files your RomM
library holds and how many of them you have. It is not a readiness score, and those files are not "optional" either: an
emulator you are not launching with may well require one.

The line says whose requirement it is on purpose, and it is a statement about the **emulator**, not about the console.
It names that emulator, and the name is the one the rest of the line was worked out for — the same pick the counts
beside it were filtered by, so the sentence and the numbers can never be about two different emulators. Where Tender
could not settle on one, the line says "The launching emulator marks none of its BIOS files as required" instead.
Whether the console itself starts without a BIOS image is weighed into the colour by the resolver and named in a line of
its own where it is why the line is red (see
[When the console needs a BIOS image](#when-the-console-needs-a-bios-image)), and where nothing is recorded about the
console Tender says nothing about it either — so a line reading only "nothing required" would have claimed an all-clear
nobody gave.

Where the line says how many of the required files are in place, it can carry two counts, and they count different
things. The sentence counts the files the emulator you launch with requires, every one of them whether your RomM library
holds it or not — a required file you cannot download is still required. The ratio in brackets, where there is one, is
always your library's: how many of the files it holds for the platform are already in place. So "2 of 3 files melonDS
opens by name are in place (3/5 RomM library files)" is one count of each kind, and the two need not agree: a required
file your library does not hold is counted in the sentence and never in the ratio, and a library file the emulator does
not require is counted in the ratio and never in the sentence. A file that reads "could not be established" is left out
of the ratio, since nothing showed it missing, and where no file could be established there is no ratio at all. A file
your library does not hold still gets its own row wherever it matters — a file this game requires, or one the console
itself needs, is listed whether your library holds it or not.

The readiness line is computed against the **active core** for that game — so switching to a core that needs no BIOS (or
that treats a file as optional) clears the warning, while switching to a core that requires a missing file surfaces it.

Under the line sits the emulator selector, which chooses which emulator's files the list shows. Its top row reads _◀ ●
YabaSanshiro 1 / 5 ▶_: it opens on the emulator you launch with, whose name is blue, and ◀ and ▶ step to the previous
and next emulator, going round from the last back to the first. **L2** and **R2**, whose glyphs stand beside the arrows,
do the same anywhere in Tender's part of the page while the **BIOS** tab is open — the play row and the tab bar
included. Under it every emulator Tender could ask about stands in a row of small names that turns like a carousel: the
one shown is always in the middle, outlined, the others on either side in the order the arrows step through them, and
pressing a name shows that emulator. Each carries a dot for that emulator's own answer (green has everything it needs,
red does not, grey not established). An emulator RetroDECK lists under two names is one entry carrying both, such as
_LRPS2 / PCSX2_ on a PlayStation 2, whichever of the two you picked. The emulator your games launch with has its name in
blue and is outlined in blue in the row of names. An emulator that is not installed is listed too, its name in red — red
even where it is the one your games launch with, which its blue outline still shows: what it would need is known all the
same, and where none of a system's emulators is one Tender can start itself, the page reads the one RetroDECK starts by
default — the first it lists, unless you chose another in ES-DE — as launching. The list shows the chosen emulator's own
files, exactly where it opens them and with its own verdict for each: with YabaSanshiro launching Saturn, the list shows
`saturn_bios.bin` directly in the BIOS folder, and with Kronos chosen `kronos/saturn_bios.bin`. Choosing another
emulator changes only the list — never which emulator launches. The line above and the **BIOS** badge beside Play stay
those of the emulator you launch with. A per-region image another emulator lacks shows a grey dot here, not a red one:
only the emulator you launch with is checked against this game's region. This page downloads and deletes no BIOS file
itself: **Download BIOS** in the game's RomM Actions menu fetches every file your library holds for the platform into
Tender's firmware store and links it for every emulator that asks for it — its notification says how many files it
downloaded, and what it could not link or moved aside is said under the rows here — and every file of every emulator can
be downloaded or deleted on the platform's page (see **Library › Platforms** below). Files in your RomM library that no
emulator was found to ask for are listed in a section of their own under the list, the same whichever emulator is chosen
(see below). A row is headed by the file the emulator declares, with its folder where it asks for one (`dc/dc_boot.bin`)
— that is where the file has to go, and it is the one thing you need when placing one by hand. Whatever the emulator's
packager wrote about the file follows the name on the same line, in the packager's own wording and punctuation —
`scph5500.bin (PS1 JP BIOS)` — and only where it says something the name does not: those descriptions usually repeat the
file name, and many are nothing else. Where the file also has something to say about its state, that comes last, behind
a dash: `scph5500.bin (PS1 JP BIOS) — missing, not in
your RomM library`. It is the **emulator's own** wording — a
RetroArch core's description file — and it is shown for no other source. Where Tender's own card stands in for an
emulator that ships no description of its own, that card explains the requirement in full sentences, which is a
paragraph on a line meant for a few words; the row shows the file and its state instead.

Under every name a small line says what the chosen emulator needs of the file — _required_, _optional_, or _one of these
· for USA discs_ for one of the per-region images below — so a file the launch needs and one it can do without read
apart even where both dots are green.

Beside the Play button there is also a short **BIOS** badge, which is a shortcut into this tab. It follows the red
verdict and nothing else: the resolver says the emulator you launch with does not have what it needs — a required file
absent or the wrong image, a required folder that holds no BIOS image, or a console that does not start without an image
and has none of the emulator's in place — or, where the console needs one image per disc region, the image for this
game's region is not in place. If that emulator requires nothing, or has everything it requires, there is no badge —
however many optional files are missing. A verdict nothing could establish raises no badge either — a file Tender could
not compare, a folder it could not read — since nothing has been shown to be missing; those cases are worth reading, but
not worth a warning next to Play, so they live in the BIOS tab. And the amber case raises none: whether a game starts
there depends on its disc's region.

The badge is always **red**. It is a warning, not a status: the four-colour dot above belongs to the tab's readiness
line, and every state that raises the badge is one where firmware the emulator needs is not in place. Having one of
three required files is not a milder version of the problem, so it does not get a milder colour, and neither does a
console that will not boot.

The badge counts the files the core requires, on their own — _1/2 required_, or _0/2 required_ beside a file nothing
could check, which is in the second number and never in the first. Where every plain required file is there and the
verdict is still red — the console's own image, or a per-region image with nothing in place — it reads _Missing_.

A BIOS warning only ever disappears on an **answer**. When a check cannot be run at all — most often right after a BIOS
download or delete, before the state has been read again — Tender keeps showing the last status it knew rather than
reporting "no BIOS needed". So a check that could not be completed never quietly clears a missing-BIOS warning and lets
the game launch without its files; it leaves the warning where it was.

"The requirement is unknown" is itself an answer, and a different one. A check that ran and could not establish what the
system needs says so on the page — grey dot, "Nothing could be established about what mGBA needs" — instead of hiding
the BIOS tab. Hiding it would say the system needs nothing, which is exactly the claim that could not be made. Every PS3
game is in that position, since RPCS3 is not a RetroArch core and cannot be asked.

It does not leave it there indefinitely, though. Whenever the game detail page is shown a BIOS state it could not work
out — when the page opens, after switching the emulator core, after switching to another version of the game — it asks
again in the background and fills the answer in a moment later. So a game opened while its requirement is still unread
gets its BIOS tab a beat after the rest of the page, and a core or version switch settles on the new core's or new
version's readiness rather than the one before it.

An unreachable RomM server usually does **not** put Tender in that position. Everything the readiness line needs is
local: which emulator is active comes from your RetroDECK configuration, what that emulator needs comes from the
emulator itself, and what you already have comes from your BIOS folder. Your server contributes the **download** — so
while it is unreachable you still see what is needed and what is missing, you just cannot fetch anything, and files that
exist only in your RomM library are not listed.

For each emulator the list shows that emulator's own files — nothing borrowed from another emulator, whose files are
shown when you choose it — and not every one of them, but the ones this game gives you something to do or to know: files
the emulator requires, the images it would start the console from (one of them per region), files you already have,
files you can still download, and files Tender could not judge. What is left over — the emulator names it, does not
require it, it is not met, and no page offers a download for it — is counted on one line instead, naming the emulator:
_6 more files Flycast lists — not required for this launch, nothing to download. All files are on the platform page._
With another emulator chosen the line names that emulator's own need instead: _not required by Kronos_.

A Dreamcast is the everyday case. Flycast also emulates Naomi and AtomisWave, so it declares six arcade BIOS files
beside the two the Dreamcast itself needs, and none of the six is anything a Dreamcast game's page could act on. The
**Platforms** tab of the **Library** page lists every file of every emulator without exception — that is where BIOS
files are managed, and it is the page the summary line points at.

With the emulator you launch with chosen, each row's dot says how that file stands for this launch: green where it is in
place, red where the launch requires it and it is not — or where it is one of the images below and its region is left
uncovered — and grey otherwise: a file the launch does not need, whether missing, the wrong image or refused, and a file
nothing could judge. Amber is never a row's colour.

Beside each file's name and its description, after a dot, a word says what the chosen emulator needs of it — _bios7.bin
(ARM7 BIOS) · optional_. Where the file is one of the images an emulator needs **one of**, picked by the region of the
disc, that word says which region it serves: _one of these · for Japan discs_, or _one of these · for every region_ for
an image that emulator starts every disc from. Whether it is in place is the dot's to say, as on every other row. None
of those images is _required_ on its own: each is what the discs of its own region start from, and the group, not the
image, is the requirement. See [When the console needs a BIOS image](#when-the-console-needs-a-bios-image) for what the
line above the list does with them.

Files in your RomM library that no emulator was found to ask for are no emulator's files, so they are listed in a
section of their own under the list, headed **In your RomM library, asked for by no emulator here**, which stays the
same whichever emulator is chosen. The word beside each name gives one of two answers — every one of those files turns
on the same question, whether the emulator you launch with could be read, so they all get the same answer:

- _not required_ — the emulator you launch with was read, and none of the emulators RetroDECK offers for the system was
  found to ask for the file. That is a finished answer for this launch, and not a promise about every emulator you have
  installed: another one Tender could not read may still want the file, and that does not move it out of this section.
- _need not established_ — the emulator you launch with could not be read, or Tender could not tell which emulator that
  is, so nothing could be said about the file either way.

A file your emulator needs that is **not in your RomM library** is listed too, marked _not in your RomM library_. Tender
cannot fetch it — nothing in it can — so it is shown for what it is rather than left out. Adding the file to RomM makes
it downloadable like any other.

Not holding a file is a different question from not having it, and the row says both. If it is also absent from your
BIOS folder it reads _missing, not in your RomM library_; if it is already sitting there it reads only _not in your RomM
library_, green where the resolver judges it the right file.

A grey row has a cause worth knowing about. Where Tender could not look at the place a file goes at all — broken
permissions, or storage going bad — the row names that place rather than calling the file missing: _Tender could not
look into bios/kronos/, so whether saturn_bios.bin is there is not known_. You know to check the folder instead of
hunting for a download, and no page offers one: Tender places nothing where it cannot see what is already there, and a
place Tender cannot read is usually one your emulator cannot read either. Once the folder can be read again, the row
says what is there by itself, and offers the download if the file really is missing.

Where a file **is** there and the emulator looked at its contents, the row says what came of that, because "not ready"
covers four quite different situations and only the last two are a problem a different copy of the file fixes:

- _the emulator does not recognise this file_ — it read the file and no list it keeps matches these bytes. That is not a
  failure: DuckStation starts such an image and calls it an unknown BIOS. It is also not a clean bill of health, which
  is why the row stays neutral rather than going green — a dump nobody has catalogued and a wrong file look the same
  from here.
- _its bytes could not be read_ — Tender asked for the contents and did not get them. This says nothing about whether
  your emulator can read the file; it says Tender could not.
- _refused — SwanStation does not accept a file of this size_ — your emulator will not even open it, so whatever is in
  it, the game will not start from it. Where the launch requires the file this is red, and the fix is a different copy
  of the file rather than a different setting.
- _wrong file — not the image Beetle PSX expects_ — the emulator compared the file with the images it knows and this is
  not one of them. Where the launch requires the file this is red, and the fix is the right copy of the file. The
  platform page's **Contents** column says the same words.

Some rows say something better than that. Where the file sitting at the destination is byte-for-byte the copy your
emulator distribution ships, the row names the distribution instead — _provided by RetroDECK_ — because it is the
distribution's file, and if it ever went missing the repair is a RetroDECK component reset rather than a download.
`dolphin-emu/Sys/codehandler.bin` is the usual example, and that one no library holds; others are perfectly ordinary
files you may well have in RomM too, and the row still names the distribution, because whose copy is at the destination
is the more useful fact. The name is printed exactly as Tender's emulator-knowledge library writes it.

Some emulators ask for a **folder** rather than a file — LRPS2 asks for `pcsx2/bios`, which is where your PS2 BIOS files
go. A folder is satisfied by what is **inside** it, never by the folder being there, so Tender opens the files in it and
reads them the way the core does, and lists the folder **one row per file**, like any other requirement:

- Each file in your RomM library that the emulator's knowledge recognises as a BIOS for that folder is a row of its own,
  with its download, its link and its **Delete**. Once downloaded it is linked into the folder under its name in RomM.
  None of them is marked required on its own: the core needs exactly one image, and the folder counts once in the
  summary.
- Each file already lying in the folder that is not Tender's, and that the emulator says something about, is a row too,
  with the emulator's verdict on it, in its own words — _USA v02.00(14/06/2004) Console 20040614-100909_ and so on — and
  nothing to press: Tender did not put it there. A file the emulator passes over because a valid image lies beside it
  gets no row.
- Where LRPS2's own settings name an image that is not in the folder, a neutral line says so, naming the emulator as the
  selector does — _ℹ LRPS2 / PCSX2's settings name scph10000.bin, which is not here — it uses another BIOS from this
  folder instead._ That is not a fault: LRPS2 falls back to the images it finds in the folder.
- A folder holding no image is red, exactly like a missing file, and it is what the red **BIOS** badge beside Play
  appears for. **Download required** then fetches one image — the first recognised one in your library, alphabetically —
  because one is all the emulator opens. While nothing lies in the folder and your library holds no file recognised for
  it, one row stands for the folder itself, with what the emulator found there and the line _No BIOS image here yet_; it
  goes as soon as a file is there.
- Where the read could not finish — a file whose bytes would not come back, a folder that could not be listed in full,
  or an image Tender's identity table and the emulator's own check disagree about — the row says so and gets a grey dot,
  and the system's readiness line declines (see [When readiness cannot be stated](#when-readiness-cannot-be-stated)
  below). Nothing is being claimed either way.

A folder that is **absent** is red like any other requirement that is not there. Tender does not create it: each row of
a library file recognised for it says _/home/deck/retrodeck/bios/pcsx2/bios does not exist yet. Start LRPS2 / PCSX2
once, then try again._, and with no such file the folder's own row reads _No BIOS image here yet_. On a stock RetroDECK
that will not happen for `pcsx2/bios`, which RetroDECK links onto the BIOS folder itself.

<!-- Screenshot: Game detail page showing red BIOS status with "2 of 3 files melonDS opens by name are in place" -->

![BIOS file list overlay showing individual required files with checkmarks and "Missing" labels](../assets/screenshot-bios.jpg)

## Library › Platforms

The **Platforms** tab of the **Library** page holds everything about one system: its sync toggle, the **active emulator
core**, the BIOS files that core needs, and the two ways to take the platform back out of Steam. The list on the left
holds every platform your RomM server reports with at least one ROM, in two groups — **Synced** above **Available** —
and the row you focus is the one the right-hand pane describes.

1. From the main QAM page, tap **Library**, then move to the **Platforms** tab with L1/R1
2. Each row is a coloured dot, the platform's name and the sync toggle. The dot is the BIOS state at a glance — green
   ready, red missing, grey not established, amber where only the disc's region decides — and the numbers behind it are
   on the right-hand pane, which also states them in full. With a mouse, hovering the row states it in a sentence — once
   the platform has been read, the same sentence the pane shows
3. The dots fill in one platform at a time, from the top, and the platform you are on is always looked at next — so the
   pane you have open does not wait behind the rows above it. Every platform in the list is looked at, whether or not
   your RomM library holds BIOS files for it and whether or not any of its games is synced. A row that is still being
   checked draws its dot as an **outline** rather than a filled circle, and its pane says "Checking what this platform
   needs…"; that is why a grey filled dot can be trusted to mean "not established" rather than "not looked at yet" (a
   read that failed says so in the pane). Checking one platform takes a tenth to half a second on a Steam Deck, so a
   library of thirty is done in a few seconds, and leaving the page stops the work
4. Move down the list to pick a platform; the pane on the right changes with the focus
5. The pane's first line names the platform. The smaller grey line under it names the emulator it launches with — by
   name, in grey when it is the platform's default and in gold when you have picked something else — then how many ROMs
   it has on RomM, how many are in Steam, and the system its games are in your emulator source ("RetroDECK system:
   psx"). If it reads **RetroDECK decides**, Tender could not pin any of this platform's emulators — they may need
   setting up, or ES-DE's command for them is not one Tender can bake — so RetroDECK chooses one itself when a game
   starts. If it reads **no emulator installed** in red, the emulator RetroDECK would have fallen back to is not on this
   machine, and the sentence below names it. If it reads **no emulator** in red, RetroDECK has none for this platform at
   all. The sentence below the line says which of the three it is. A game's BIOS tab reads the same words in its
   Emulator column, the red ones in red, when nothing can be pinned for that game
6. The **chip button** at the right of the name opens a menu of the platform's emulators — the same button, in the same
   two colours, as the one on a game's page. It is always there: it opens the menu whenever there is more than one
   emulator to choose between — also before the platform's first sync, so you can pick before its games reach Steam —
   and is greyed out otherwise, with the reason shown if you hover it. Where the reason is a problem rather than simply
   nothing to choose — no emulator for the platform, or an emulator list that could not be established (no emulator
   source found, every source switched off, a broken ES-DE systems file, a RetroDECK that has not been set up yet, a
   list that cannot be read yet, or not established for another reason) — a line under the header says so as well, since
   a tooltip needs a mouse. A platform your emulator source has no system for says **"_Source_ has no system for
   _platform_, so Tender cannot download its games."**, and one whose only systems are switched off there says **"System
   _system_ is switched off in _source_."**, naming the source that answers — see
   [Managing Games](managing-games.md#downloading-roms). Where Tender has not learned a platform's system yet and RomM
   cannot be reached to tell it, the line, a game's BIOS tab and its emulator menu say **"RomM cannot be reached, so
   Tender does not know this platform's system yet."**; where RomM answers but does not give it — a failed sign-in, say
   — they say **"RomM did not give this platform's ids, so Tender does not know its system yet."**
7. **BIOS files** states how many required files are ready (e.g. "1 / 2 required") when the system needs any, and
   otherwise names the emulator and says it marks none of its BIOS files as required. Under that heading is the same
   sentence the game page shows, with your RomM library's own inventory behind it (e.g. "(3/5 RomM library files)").
   Where no emulator can be pinned for the platform there is no name to print, and the line says "The launching
   emulator" instead. A console that needs one BIOS image per region is counted in regions rather than files: "3 / 3
   regions" where every region has its image, "1 / 3 regions · USA only" where only some do, with the line under it
   naming the regions whose discs will not start, and "0 / 3 regions" where none does. An emulator that requires plain
   files as well counts those first and the regions after them — see
   [When the console needs a BIOS image](#when-the-console-needs-a-bios-image). A system with a required row Tender
   could not judge — a declared folder it could not read, say — reads "Readiness unknown" instead — see
   [When readiness cannot be stated](#when-readiness-cannot-be-stated). Everything here is about the emulator named on
   line 5: pick a different one from the chip button and the numbers, the dot and the file rows are answered for it, so
   this pane and a game's BIOS tab tell you the same thing about one platform. That holds for a **standalone** emulator
   too — PCSX2, DuckStation, Cemu and melonDS are asked like any RetroArch core. Where Tender has no source for the
   emulator, the files every other emulator declares are shown when you choose that emulator (step 8). Each row is that
   emulator's own requirement, at the place it opens the file: with YabaSanshiro picked for Saturn, `saturn_bios.bin` is
   the file directly in the BIOS folder, and one lying under `kronos/` — where Kronos looks — does not count for it
8. Below it, the same emulator selector as on a game's page chooses which emulator's files the table shows — _◀ ●
   YabaSanshiro 1 / 5 ▶_ over the carousel of every emulator — opening on the emulator the platform launches with, each
   emulator with a dot for its own answer. **L2** and **R2** step through the emulators wherever you are on the page,
   the list of platforms on the left included. Choosing another never changes what launches, and the summary above stays
   the launching emulator's; what follows the chosen emulator is the table, its rows' own buttons (step 10) and
   **Download required for _emulator_** (step 11). Another emulator's rows look and act like any other. The table lists
   the files themselves: the **file**, whether it is **on disk**, and its **contents**. Where the emulator asks for the
   file in a subfolder, the folder is shown in front of the name (`dc/` **`dc_boot.bin`**) — that is where it has to go,
   and it is the one thing you need when placing a file by hand. The description in parentheses is printed under the row
   rather than beside the name, as it is on a game's page: this column is narrow enough that anything after the name
   would push the name itself off the row. Like there, it is shown only where the emulator itself wrote it. Above it,
   first under the name, a small line says what the chosen emulator needs of the file: _required_, _optional_, or _one
   of these · for USA discs_ for a per-region image (the library's own section, step 12, has two words of its own). On
   disk holds marks and no words. The first mark carries two things — a green ✓ for a file that is there and required, a
   red ✗ for one that is required and is not, and the paler green ✓ / grey ✗ for a file the chosen emulator does not
   need either way. Whether a file is "there" is the resolver's verdict, not just its presence: a file that is the wrong
   image reads ✗, and one the resolver could not compare reads `?`. A ✗'s tooltip and legend line say which it is —
   _missing_, _wrong file_ or _refused_ — in the words the Contents column uses. A grey `?` means not established,
   either way: a row whose verdict nothing could settle — including a place that could not be looked at, which is never
   read as missing — and a file whose need nothing could establish, because no emulator was found to ask for it and the
   emulator you launch with could not be read or named. Its tooltip and the legend still say whether that file is there.
   Amber is never a mark here. Where the system needs **one image per region** (see
   [When the console needs a BIOS image](#when-the-console-needs-a-bios-image)) each of those images is marked as one of
   the group: a green ✓ where it is there, a red ✗ where it is not — that region's discs have nothing to start from —
   and the line under its name says which region it serves: _one of these · for USA discs_. A violet ⊘ appears
   **beside** that mark — never in place of it — when your RomM library does not hold the file: a file you already have
   keeps its green ✓, one you still need keeps its red ✗, and the ⊘ adds that Tender cannot fetch it for you. A legend
   under the last table (step 12), one line per mark, names the marks that are actually on the tables. Anything else a
   row has to say is printed **under** the row rather than in the column — that a file was provided by RetroDECK, that a
   folder holds no image, that a place could not be looked into (and so is offered no download)
9. **Contents** says what the resolver's check found at the file's place, in its own terms: **right file**; **wrong file
   — not the image … expects**; **refused — … does not accept a file of this size**; **not comparable — this kind of
   file changes with the emulator version**; **not recognised**; **could not be read**; **not checked**; or **not
   established — the resolver could not tell**. It explains the mark beside it and never contradicts it: a wrong or
   refused file reads ✗, one that could not be compared or recognised reads `?`. A file that is not there reads an em
   dash — nothing was there to check. For a required **folder** it says how many BIOS images the folder holds — and the
   images themselves are listed under the row, in the emulator's own words so you can match one against its picker — or
   that it holds none, or that its contents could not be established. Where a cell is too narrow for its words, they
   wrap onto the next line
10. A **Download** button sits on every row that your RomM library fills and that Tender's firmware store does not hold
    yet, and a **Delete** button on every row whose file Tender downloaded for this platform and still has (see
    [Deleting BIOS Files](#deleting-bios-files)). Both are on every emulator's rows. Where another file already lies at
    the place and the emulator does not accept it, the row offers **Replace…** instead, when your library holds a copy
    that is proven right (see [When a wrong file is in the way](#when-a-wrong-file-is-in-the-way)). While a download
    runs, the button you pressed becomes a spinner and the other download buttons grey out; when it finishes the list
    re-reads itself. **If it fails**, that button turns red and says **Failed** for about two seconds — the other
    buttons stay greyed until it clears — and a line under the section says what went wrong
11. Three buttons fetch or remove several files at once. Under the table, **Download required for _emulator_ (N)**
    follows the chosen emulator: it fetches the files that emulator requires and, where the console needs one image per
    region, the images of the regions that have none yet — nothing more once every region has its image — and the number
    on it counts exactly what it would fetch. Where Tender could not identify the emulator the platform launches with,
    the list opens on the first other emulator it could ask, and the button is that one's; where there is no emulator to
    ask at all, there is no such button. The other two act on the whole platform whichever emulator is chosen, under a
    small heading of their own inside the BIOS section, **For all of _platform_**: **Download all** fetches every file
    your library holds for the platform, also one no emulator asks for, and **Delete downloads (N)** removes every BIOS
    file Tender downloaded for the platform (see below). Apart from that one case, all three are always there and grey
    out when there is nothing to do. What they follow is your **library**, not the readiness line above them: a system
    Tender could work out nothing about still offers everything your library holds for it, because those are two
    separate questions — see [When the requirement is unknown](#when-the-requirement-is-unknown). The two downloads
    carry on past a file they cannot fetch: the line under the section then names the files that failed, and the button
    says **Failed** only when not a single file could be fetched. Where a link could not go somewhere because another
    file already lies there, the line says how many were placed and how many were left: _3 placed, 1 left in place:
    another file lies there_
12. Below them, files in your RomM library that no emulator was found to ask for are listed in a table of their own,
    under the small heading **In your RomM library, asked for by no emulator here** — the same section as on a game's
    page, and the same whichever emulator is chosen. The line under each name reads _not required_ where the emulator
    the platform launches with was read and nothing asks for the file, or _need not established_ where nothing could
    answer for it. Once the page is shown, Tender asks what each of these files is, one at a time: the row reads
    _Checking…_ until the answer arrives, then _Recognised as_ every name the file is known under, or _Not recognised_.
    That answer only labels the row; it changes no colour and no count. A file of these that Tender downloaded reads _In
    Tender's firmware store, used by no emulator_, and is linked as soon as an emulator asks for it. Its rows offer
    **Download** and **Delete** like any other

**Download BIOS** in a game's RomM Actions menu carries on past a file it cannot fetch the same way, and names the
failed files in its notification.

<!-- Screenshot: Library › Platforms with a platform selected, its core button above the BIOS table -->

### Where downloads go

A BIOS file is downloaded into **Tender's firmware store**, a folder of its own inside
[Tender's folder](where-your-data-lives.md): `Tender/bios/<platform>/`, under the file's name in your RomM library. A
file two platforms need lies in each of their folders. Tender then places a **link** to it at every place an emulator of
the platform opens it, under the name that emulator asks for, in every emulator source you have switched on —
Dreamcast's `dc_boot.bin` goes into `bios/dc/`, Cemu's `keys.txt` into Cemu's own folder. The file is there once, and
every emulator that needs it reads the same copy.

Every download is checked against the checksum RomM states for the file before Tender keeps it. A download that does not
match is thrown away, nothing changes, and the row says _The download was damaged. Try again._ A file RomM states no
checksum for is never downloaded: its row says _RomM states no checksum for scph5501.bin, so Tender cannot check a
download._

Tender keeps its links up to date by itself whenever you open a platform's page or a game's page, and after you change
the emulator a platform or game launches with: it places every link an emulator of the platform wants from the store,
puts back one of its links that was removed, and removes one whose file is gone. It never downloads anything for that —
a download is only ever made when you press a button. Until that is done a row whose file is in the store reads _In
Tender's firmware store, not linked here yet_.

A link is only ever placed where the name is free. Where a file, a folder or a link that is not Tender's already lies
there, Tender leaves it as it is, and the row says what the emulator makes of it: _Another file lies here, and the
emulator does not accept it_, or _Another file lies here; it could not be checked_. A right file you placed yourself
simply reads met, and nothing is offered. Where another platform's copy already serves the place — a boot ROM Game Boy
and Game Boy Color both look for in the BIOS folder — the row reads met with _Linked to Tender's copy for Game Boy_ and
still offers a download into this platform's own folder.

Some places are left alone for a reason the row names:

- _/home/deck/retrodeck/bios/dc does not exist yet. Start Flycast once, then try again._ — the folder the emulator opens
  is created by the emulator, not by Tender.
- _RetroDECK's settings could not be read, so Tender places nothing in /home/deck/retrodeck/bios._ — while RetroDECK's
  settings cannot be read, the places under RetroDECK are only guesses, and Tender writes nothing into a guess. Places
  read from the settings, and places outside RetroDECK, are used as usual.
- _Could not place a link at …: …_ — placing the link failed, and the reason follows. It is said by the latest attempt
  on the page and is gone once you leave it.

Where the emulator the platform launches with is one Tender's emulator knowledge does not cover yet, or no emulator
source is set up for the platform, a download lands in the store only, and one sentence above the table says so:
_Ryubing is not supported yet. Downloads are kept in Tender's firmware store and linked once it is._, or _No emulator
source is set up for PC-98. Downloads are kept in Tender's firmware store and linked once one is._

BIOS files Tender downloaded straight into RetroDECK's BIOS folder before the firmware store existed stay where they
are, and their **Delete** still removes them there.

### When a wrong file is in the way

Tender never overwrites a file it did not put there, and **Download required** leaves such a file in place. Where the
emulator reads the file at a place as the wrong image, or refuses it, and your RomM library holds a copy whose checksum
is the one the emulator expects, the row offers **Replace…**, on the platform's page and on a game's BIOS tab, where you
select the row itself and press A. Its confirmation names the exact path and where the old file goes: _The file at
/home/deck/retrodeck/bios/scph5501.bin goes to /home/deck/.local/share/romm-tender/backups/bios, and Tender's copy is
linked in its place._ Only once you confirm is the old file moved to Tender's backup folder — `backups/bios/` in
[Tender's data folder](where-your-data-lives.md), under the date and time, followed by the file's original path — never
deleted, and the link placed. Afterwards the row says _Replaced. The old file is in …_ until you leave the page. Without
a proven-right copy in your library the row stays red with its sentence and nothing is offered, and a file the emulator
accepts, or that could not be checked, is never replaced.

A file lying in the store folder that Tender has no record of — copied there by hand, say — is taken over without a
download when its checksum is RomM's; otherwise it is moved to the same backup folder before the download, and the line
under the section says so: _A different scph5501.bin was already in Tender's firmware store. It was moved to …_

### Deleting BIOS Files

You can remove BIOS files from a platform's pane in **Library › Platforms**, one at a time or all at once. Both only
ever remove what Tender downloaded; because deletion is local, both work with your RomM server offline.

The **Delete downloads** button under **For all of _platform_** takes every BIOS file Tender downloaded for the
platform, and its label shows how many (e.g. "Delete downloads (3)"). Its confirmation reads _Delete Tender's BIOS
downloads for Saturn?_ — _Tender removes every BIOS file it downloaded for Saturn and every link to them. Files you
placed yourself stay._ It is always there and greys out when there is nothing to remove.

Each row also carries its own **Delete** where Tender downloaded that row's file for this platform, whichever emulator
is chosen; each file of a folder the emulator reads (PS2's `pcsx2/bios`) has its own. Its confirmation reads _Delete
saturn_bios.bin?_ — _Tender removes it from its firmware store and every link to it. Files you placed yourself stay._ A
file Saturn's Kronos and YabaSanshiro both want lies once in the store with a link at each emulator's place, and one
**Delete** removes the file and both links.

1. In **Library › Platforms**, pick the platform whose BIOS files you want to remove
2. Tap **Delete downloads**, or the **Delete** on a single row
3. Confirm the action in the dialog that appears — every one of them asks first

Once confirmed, Tender removes the files and reports the result. Where a file cannot be removed, the line under the
section says so and the list re-reads itself: the rows that still offer **Delete** are the files that stayed. A link
under RetroDECK's folder while RetroDECK's settings cannot be read stays where it is, and the line under the section
says so, once per folder: _Left a link in /home/deck/retrodeck/bios: RetroDECK's settings could not be read. Tender
removes it once they can be read._ Tender removes it the next time it brings its links up to date with the settings
read. A file an older version of Tender downloaded straight into that folder stays too, says so — _Left dc_boot.bin in
/home/deck/retrodeck/bios: RetroDECK's settings could not be read. Delete it again once they can be read._ — and keeps
its **Delete**: Tender removes it only when you press it again. Games that need those files won't launch until you
download them again with **Download all** or **Download required for _emulator_**.

**It only ever deletes its own downloads and links.** A file is removed when Tender has a record of downloading it, a
link when it points at a file of Tender's firmware store that Tender has a record of, and for no other reason.
Everything else in the BIOS folder is left alone, including:

- **Firmware RetroDECK ships itself.** RetroDECK installs some files into the BIOS folder with its own components —
  `bios/dolphin-emu/Sys/codehandler.bin` is one. Your emulator asks for it, so it is listed on the page, marked
  _provided by RetroDECK_, but Tender did not put it there. That one in particular is in no RomM library either, so
  nothing here could fetch it back.
- **Files you placed there by hand**, even where the name matches one your RomM library holds. Without a download record
  Tender has no claim on it.
- **Files you replaced with Replace…** stay in the backup folder until you delete them yourself.

Being in your RomM library is neither necessary nor sufficient. A file you have since removed from RomM is still deleted
if Tender downloaded it — otherwise its own downloads would be stranded on disk with no way to clean them up — and a
file that is in your library but arrived some other way is left where it is. If you want one of those gone, delete it in
the file manager.

The count on the button is the same set: it counts every file in the platform's firmware store and every older download
of Tender's still on disk — a copy only another emulator uses, and a second copy of the same bytes, included — so it
matches what the delete reports.

This is the only place a platform's BIOS files are deleted. The Data Management page used to offer the same action
without a confirmation; that copy is gone.

## Which Systems Need BIOS?

This depends on your emulators, not on your library. Common systems that require BIOS files include PlayStation, PS2,
Saturn, Dreamcast, and some arcade systems. Every platform in **Library › Platforms** gets a BIOS status, whether or not
your RomM library holds firmware for it — so a missing BIOS you have never uploaded is visible rather than silent.

### Where Tender gets its answers

Tender asks the **emulators RetroDECK offers for that system** what they want — every one it lists, RetroArch cores and
standalone emulators alike. A RetroArch core ships a small description file next to it declaring the firmware it needs
and where each file goes. A standalone emulator (PCSX2, DuckStation, Cemu, melonDS, xemu) states its firmware in its own
format instead, and for those Tender uses a packaged rule card that reads the emulator's own settings. Both are read
live, so the answers follow your RetroDECK install, including emulators added after your version of Tender was released.

That same reading also answers whether a **declared** file is already sitting where the emulator will look for it, and
for those placed under your BIOS folder Tender takes its answer rather than checking the path itself. The difference
matters on a stock RetroDECK: `bios/pcsx2/bios` is a link pointing back at the BIOS folder, so working the location out
from where a link ends up loses the folder the emulator actually opens. Following the emulator's own spelling gets the
check right. The rest Tender looks up itself, in three cases. A file in your library that no emulator RetroDECK offers
for the system was found to declare has no reading to take. A file whose declared location Tender cannot place under
your BIOS folder — a standalone emulator keeping its firmware in a folder of its own, for example — is checked where
Tender would put it itself, since the reading is about somewhere else. And every BIOS download Tender offers — one file
or several at once — checks each destination before fetching, because the reading was taken before the files it fetches
arrived.

The reading **opens files and reads them**, which is what two answers need. Where an emulator asks for a **folder**
rather than a file, the folder being there settles nothing — a folder is satisfied by what is in it — so the candidates
inside it are read the way the emulator reads them. And a rule card may identify its BIOS image by its contents rather
than by name, so until the bytes are read it names no file at all. This is why the question is asked one system at a
time: reading your whole BIOS folder every time a game page opened would be far more work than reading the handful of
files one system's emulators care about.

**Not every standalone emulator can be answered for.** The packaged cards cover five of them today; the rest are
installed emulators Tender has no source for, and it says so rather than guessing — a system launching one of those
reads **unknown** (below), never "not needed".

Each file on your server therefore gets one of four answers:

- **Needed** — an emulator RetroDECK offers for this system marks it required
- **Optional** — an emulator RetroDECK offers for this system asks for it, and none of them marks it required
- **Not needed** — no emulator RetroDECK offers for this system was found to ask for it, and the one this system
  launches with was asked
- **Unknown** — no emulator RetroDECK offers for this system was found to ask for it, and the one this system launches
  with could not be asked (see below)

Needed and optional describe the file across every emulator the system offers, not the one you launch with. Whether that
emulator requires the file is a separate question, and it is the one the readiness line answers.

### When the requirement is unknown

"Not needed" and "unknown" are deliberately kept apart. The first is an answer; the second is the absence of one, and
Tender will not present it as an all-clear.

The doubt is scoped to **the emulator your games will actually launch with** — the one named at the top of the platform
page. An emulator RetroDECK also offers, that Tender could not read, says nothing about a launch that does not use it,
so it does not grey out the answer. The other side of that: switching a platform to an emulator Tender has no source for
moves it from a finished answer to **unknown**, which is the truth about the new emulator rather than anything having
gone wrong with the old one.

A **file** reads unknown when no emulator RetroDECK offers for the system was found to ask for it and the emulator you
launch with could not be asked, for any of the reasons listed below. A file another emulator does ask for keeps that
answer — needed or optional — whichever emulator you launch with.

A whole **system** reads unknown, showing a neutral grey status and a sentence naming the emulator that could not be
asked — **"Nothing could be established about what PCSX2 needs"** — instead of a green all-clear, in either of two
situations.

The first is a system with files on the page whose launching emulator could not be asked. Rows that other emulators
declare keep their answers, but none of them speaks for this launch, which is what leaves nothing to base a readiness
claim on. A system whose every file was answered with _not needed_ is not this case at all — that is a finished answer,
and it reads green.

The second is a system with **no files on the page at all**, where the launching emulator could not be asked either. An
empty list means "nothing here wants anything" only when the emulator was asked; with it unread the list means nothing,
and reporting it as ready would be an all-clear over firmware nobody checked.

Both shapes, and a file reading unknown, come from the launching emulator not having been asked, and that has more than
one cause:

- a standalone emulator with no packaged card, or one whose card named no file
- a RetroArch core that ships without a description file — on a stock RetroDECK that is rare, since only a handful of
  bundled cores are in that state
- an emulator whose declaration Tender could not follow to a location
- a launching emulator Tender cannot name — a configuration it could not read at all leaves it unable to name one in the
  first place
- no answer at all: no emulator installation found to ask, or the reading of the installation itself failing

Under that headline the page names the emulator your games on that system launch with — _Nothing could be established
about what PCSX2 needs_ — and, where Tender could not settle on an emulator to name for that system, says that instead:
_Nothing could be established about what the launching emulator needs_. Your games still launch either way: where Tender
names no emulator, it hands the launch to RetroDECK and RetroDECK picks one. Either way the sentence is about **one**
emulator. Others you have installed may have answered perfectly well, and where they did, choosing one in the emulator
selector above the list shows its answer.

Where Tender could not get the system's emulator list at all, the platform page says why under the platform's name (see
**Library › Platforms** above), and a game's **BIOS** tab says it in the same words, on the line above its readiness
sentence.

This is informational, not an error: your files may be perfectly fine, Tender simply can't confirm what is needed.
Genuinely BIOS-free systems (such as the NES) are unaffected — the emulator answers, it wants nothing, and the system
reads as ready.

#### Such a system still offers its downloads

The page adds one line — _You can still put BIOS files in your BIOS folder by hand._ — because the summary above it
cannot tell you which files to place, and hand-placing one works regardless. It is left out under _No BIOS is chosen in
PCSX2 (Standalone)'s settings…_: there the setting is what is missing, and placing a file changes nothing.

What it does **not** do is take the download buttons away. Which files an emulator wants and which files your RomM
library holds are two separate questions, and neither answers the other: a system nobody could speak for still has a
library behind it, and fetching from it is the one action that moves the system along at all. **Download all**,
**Download required for _emulator_** and every row's own **Download** follow your library exactly as they do on a system
that answered. The one exception is one of the causes above: where Tender cannot name the emulator the platform launches
with, the list for that emulator shows no **Download required**, because there is no emulator whose requirement it could
fetch (step 11 of **Library › Platforms** above); **Download all** and the rows' own buttons stay.

That matters most on the systems most likely to be in this state. PS2, GameCube and PSP launch standalone emulators
Tender has no card for, so their readiness line declines — while your library may well hold every file they need. A page
that withdrew the downloads there would leave you with nothing to press on exactly the systems that need the files most.

What the page also does **not** say is that BIOS management is unsupported here: that would describe Tender, and Tender
is not the limitation. Install an emulator that declares firmware for this system and the page answers for it, with
nothing changed on our side. Downloading the files from RomM's own web interface and dropping them in your BIOS folder
works exactly as it always did; nothing about the files changes, only what this page will claim about them.

!!! note "Systems that used to read \"Not managed by the plugin\" will have moved"

    That state meant only \"no entry in the built-in table\", and the table is gone. Those systems now get whatever
    their emulators say, which can go three ways: **green**, where the emulator you launch with requires nothing you do
    not have, **red**, where it requires a file you do not have — or the console needs a BIOS image and none is in
    place — and the table simply never knew, or **grey**, where Tender could not settle an answer. All three are
    new, and all three are honest.

### When the console needs a BIOS image

Some consoles do not start at all without a BIOS image — the PlayStation is the standard example. A RetroArch core has
no way to say that. Its description file marks each file it wants **required** or **optional**, and nothing more, so a
core like SwanStation marks every PlayStation BIOS image _optional_ — which is true of each file on its own, because any
one of them will do, and misleading about the console, which needs one of them.

Where Tender's emulator-knowledge library knows how a core picks its image, it states the requirement the way the core
boots: **one of these images**, picked by the region of the disc. A PlayStation disc is from Japan, the USA or Europe —
the regions RomM names a game by, and the ones the BIOS descriptions name (_PS1 US BIOS_) — and Beetle PSX opens
`scph5500.bin` for the first, `scph5501.bin` for the second and `scph5502.bin` for the third. SwanStation opens the
image its settings name for the disc's region and, where that is not there, searches your BIOS folder for any image it
knows — so one image it finds can serve every region.

That group is **one requirement**, however many images it lists, and the Library page's platform pane counts it in
regions — how many of the regions the core names have their image — rather than as files:

- **Green — "SwanStation has a BIOS image for every region"**, _3 / 3 regions_. Every region the core names has an image
  — one image that serves every region, or one image per region, or a mix: SwanStation may open `scph5500.bin` for Japan
  and `scph1001.bin` for USA and Europe.
- **Amber — "Beetle PSX has a BIOS image for USA only — Japan and Europe discs will not start"**, _1 / 3 regions · USA
  only_. Some regions have their image and the others are shown not to, and nothing else is open: whether a game starts
  depends only on its disc's region. It is amber rather than red, because a USA disc will start — and this is the only
  thing amber ever means.
- **Red — "Beetle PSX cannot start this system without a BIOS image"**, _0 / 3 regions_. No image of the group is there.
- **Grey — "Whether the BIOS image Beetle PSX needs is in place could not be checked"**. An image is there and could not
  be read, or a region could not be checked at all — a folder that would not list, say — or something else the emulator
  requires could not be settled. Then not even the region decides it, so the line is grey and not amber, even with one
  region's image in place; the rows keep their own marks. The page never shows green over a region nobody checked. The
  pane's short form stays "Readiness unknown" here, unless a missing plain file words the line (below).

Where the emulator states no image at all for a region, the platform pane — and the page of a game with no clear region,
which shows the platform's answer — names that region after whichever of these lines stands: "… — for USA discs Beetle
PSX states no BIOS image, so whether they start could not be established". Tender's emulator-knowledge library leaves a
region without an image where it could not read the files that would answer it (nobody was asked to check their
contents, or the folder would not list), or where the image the settings name for that region could not be followed and
the folder search found nothing either. The line stays grey, unless no image of the group is in place at all, which is
red; and where the only regions left open are those, it says what is covered and keeps the count: "Beetle PSX has a BIOS
image for Japan — for USA and Europe discs Beetle PSX states no BIOS image, so whether they start could not be
established", _1 / 3 regions · regions not checked_. A game of such a region reads grey on its own page.

A region nobody could check is never counted as having its image: it is in the second number of the region count and not
in the first. Where the emulator requires plain files beside the group, those are counted first, on their own, and the
regions after them — "1 / 1 required · 1 / 3 regions · USA only". A group nothing could check is not counted there at
all, so it never reads like one with no image in place: "0 / 1 required · regions not checked".

Each image of the group is its own row, and what it says of its need — under its name on the Library page, beside it on
the game page — says which region it serves: _one of these · for Japan discs_. Whether it is there is said on the
Library page's platform table by a red ✗ or a green ✓ in its On disk column, and on the game page by its dot. An image a
core found by searching the BIOS folder is described _found in the BIOS folder_. A file the core also lists in its own
description file under the same name is still one row, not two; an image the core found under another of the names it
tries is a row of its own.

**The game page answers for the game's own region.** RomM records which regions a game was released in, and Tender reads
them as the console's disc regions: USA and Canada as USA, Japan as Japan, and Europe, the European countries and
Australia as Europe. Everything else the emulator requires decides first: a required file that is not in place makes the
page red, and one that could not be checked makes it grey, whatever the game's region. Otherwise the image for the
game's region decides. With a Japanese game and only a USA image the page reads red — _Beetle PSX needs scph5500.bin to
start this game (Japan) — it is missing_ — and the **BIOS** badge appears beside Play. With a USA game it reads green —
_The BIOS image Beetle PSX needs for this game's region (USA) is in place_. A game released in several regions is
covered where any of them is, and where none is the sentence names the file of each, and only the regions those files
serve — a region no image is listed for gets a clause of its own: _SwanStation needs scph5502.bin to start this game
(Europe) — it is missing, and there is no BIOS image for USA_. Where the image that covers the game is one the core
starts every disc from, the page names it instead — _SwanStation starts this game with scph1001.bin — found in the BIOS
folder, it serves every region_ where the core's own search of the BIOS folder found it, and _SwanStation starts this
game with scph1001.bin, which serves every region_ where its settings named it. Where the image for the game's region
could not be checked, or the emulator states no image for that region at all, the page is grey and names the region —
_SwanStation states no BIOS image for this game's region (USA), so whether it starts could not be established_ — even
where every image it does state is in place.

Under that line the game page lists the group itself, under a subheading of its own — **BEETLE PSX · ONE IMAGE PER DISC
REGION**, named after whichever emulator the game launches with — one indented line per option, in the order the core
states them whichever game you are looking at, with this game's region marked where it stands; an image that serves
several regions is one line naming them all. For a European game: _Japan · scph5500.bin · missing_, _USA · scph5501.bin
· in place_, _Europe · scph5502.bin · missing ← this game's region_ — each with a dot, red for this game's region where
it is missing and grey for another region's. An image that serves every region is that one line alone under the same
subheading: _every region · scph1001.bin · in place ← this game's region_. The emulator selector and the file list
follow under the group, with no subheading of their own. A game whose region does not map — World, Asia, Korea, Brazil,
or none recorded — gets the platform's answer, and no badge for a group that is only partly covered: the image that is
there may be the one it needs.

On the platform page, **Download required for _emulator_** fetches the chosen emulator's images of the regions that have
none yet, where your RomM library holds them, and nothing more once every region has its image. The number on the button
counts exactly those.

Where a core states no such group, the console's own record still counts: the resolver weighs it into its verdict, so a
core whose console will not start without an image, with none of the images it knows in place, reads red —
**"PlayStation does not start without a BIOS image, and none of the five SwanStation knows is in place"**, under the
Library page's **Needs a BIOS image** heading — and raises the badge. Which image is in place is read off each file's
own verdict, so a file that is there but is the wrong image does not count. PCSX ReARMed ships its own built-in
replacement for the PlayStation BIOS, so the same page with that core selected reads green.

Where the resolver records nothing about a console, no sentence about the console appears, and this is deliberate: no
record means nobody has checked that console, which is not the same as "this console needs nothing".

### When readiness cannot be stated

There is a second grey state, and it is a different sentence: **"One file melonDS opens by name could not be checked"**.
Here Tender knows perfectly well what the system needs — it is whether you **have** it that could not be settled for one
of the required things. The verb follows how the emulator finds its BIOS, as in the counts: "opens by name", "lists as
required", or "requires" for one that also searches its folder by content.

The usual cause is a required **folder** Tender could not read all the way: a file inside it whose bytes would not come
back, a folder it could not list in full, or an image its identity table and the emulator's own check disagree about. It
is not the ordinary state of a PS2 system — a folder that reads cleanly is answered green or red like any other
requirement.

Other causes: a required file that is there and could not be compared with the image the emulator expects; a file the
emulator asks for at a place outside the BIOS folder, or one Tender could not follow, which the line names with the
resolver's reason, in the same verb as the count; a BIOS no one has chosen in the emulator's own settings ("No BIOS is
chosen in PCSX2's settings, and no game boots until one is"); or one of the images of a per-region group that could not
be read (the previous section). The requirement is known; only the answer is not.

What Tender will not do is guess at the part it could not reach. A folder whose listing broke off part-way might hold a
BIOS image in the part that was never read, or might not; calling it ready and calling it empty are both claims about
files nobody looked at. It declines instead, and says so.

What that state does **not** do is flatten the rest of the page:

- The **file rows keep their own answers.** A file the resolver judges met is still a ✓, one it judges not met still a
  ✗, and only the row nothing could be established for reads a grey `?`, with the reason beside it.
- **Downloads stay.** Every file your library holds is still fetchable, and fetching them is the thing that actually
  gets a PS2 system running. That holds for the state above too: what your library offers never depends on what Tender
  could work out about the emulator.
- The system is **not** flagged "BIOS needed", because that would be a claim too.
- The red **BIOS** badge beside Play does not appear: it follows the red verdict, and a row nobody could judge is not a
  missing file.

## Which Files a Platform Lists

A platform's list has two halves. The first is what your **server** files under that platform — a GBA page lists the
firmware your server holds under GBA, not what it holds under Game Boy. The second is every file an **emulator RetroDECK
offers for that platform** asks for, wherever your server keeps that file and even when your server does not hold it at
all; rows in that half are marked as not being in your RomM library.

The second half follows what you can **launch** the platform with, not how a file is usually catalogued, and the two do
not always line up. RetroDECK offers NooDS under Game Boy Advance, and NooDS does run GBA games. Its description file
names five firmware files — `bios7.bin`, `bios9.bin`, `firmware.bin`, `nds_sd_card.bin` and `gba_bios.bin` — and a core
states its firmware once, for the whole core, with no way to say which of those a GBA game in particular needs. So a GBA
page lists all five: they are exactly the files an emulator you can start a GBA game with declares. Filtering rows out
by the system a file is usually filed under would, wherever such a file is genuinely required, leave you with a game
that refuses to launch and nothing on the page explaining why.

Being listed is not the same as being needed. All five of NooDS's files are optional, so none of them turns a GBA page
red by itself — and what each row means for the core you actually launch with is the next section's question.

## Active Core Detection

Different emulator cores can have different BIOS requirements for the same platform. Tender detects which core RetroDECK
is actually configured to use and filters the BIOS list accordingly, so you only see the files that matter for your
setup.

### Example: Game Boy Advance

- With **mGBA** (RetroDECK's default), `gba_bios.bin` is shown as _optional_ — mGBA has a built-in high-level BIOS
  replacement
- With **gpSP**, `gba_bios.bin` is shown as _required_ — gpSP cannot run without it

The active core name appears on the game detail page (the **Emulator** column) and on the platform's header line in
**Library › Platforms**. This tells you at a glance which core Tender is filtering for.

**How the core is determined:**

1. If you set a **per-game core** for this game in Tender, that wins. (Per-game cores are stored by Tender itself — see
   [Per-Game (Game Detail Page)](#per-game-game-detail-page) below.)
2. If no per-game core, Tender checks for a **per-platform core** you set in Library › Platforms — stored by Tender in
   its own settings, not in ES-DE.
3. Tender reads RetroDECK's ES-DE configuration (`es_systems.xml`) from the flatpak installation to find the default
   emulator for each platform — the first one it can launch with, RetroArch core or standalone. This live file is the
   only source; there is no bundled fallback snapshot.
4. If the live configuration can't be read, or Tender has no source for whatever that platform launches with, it has
   nothing to filter with — so it does not filter, and it does not guess either. Every BIOS file the platform has is
   listed, each marked _unknown_, and the platform's summary reads **Requirement unknown** with a grey dot — including
   when the platform has no files to list, which is where saying nothing at all would have read as "nothing needed".
   That is the honest answer for a platform like PS3, whose emulator Tender has no card for: saying nothing is needed
   would report it ready over firmware the emulator will not boot without. The download buttons are unaffected — they
   follow your library, which is a different question — see
   [When the requirement is unknown](#when-the-requirement-is-unknown).

Whatever this chain resolves to is the **same core the game launches on** — Tender bakes the resolved core into the
Steam shortcut, so the core shown for BIOS, saves, and the core badge always matches the core that runs.

The detection chain ensures BIOS filtering works even when RetroDECK's configuration files aren't accessible (e.g. after
an update changes paths). You'll see a "Core: mGBA" badge when detection is working, or no badge when falling back to
showing all files.

## Changing the Active Core

You can change the active emulator core directly from Tender, without leaving Game Mode. There are two scopes, and
**both are stored by Tender itself** — neither touches ES-DE's `gamelist.xml`. Tender bakes the chosen core directly
into each game's Steam shortcut, so your choice applies reliably for any ROM filename.

- **Per-platform** changes set the core for every game on a platform. Stored in Tender's own settings.
- **Per-game** changes set the core for a single game and take priority over the platform choice. Stored by Tender on
  the game, so they survive uninstalling and re-downloading.

### Per-Platform (Library Platforms tab)

In **Library › Platforms**, a platform with more than one emulator shows a **chip button** at the right of its header
line — the same button a game's page carries, grey when the platform is on its default emulator and gold when you have
picked another. It opens a menu listing every emulator ES-DE offers for that platform — both RetroArch cores and
**standalone emulators** (e.g. PCSX2, RPCS3, Dolphin, PPSSPP). Some entries appear **disabled** with a short reason (for
example "script/shortcut form" or "Tender can't start this entry") when Tender can't launch them directly from Steam;
those can't be picked. Picking an enabled emulator sets it as the default for all games on that platform. A "Switching
cores may affect save compatibility" note is the first line of the menu.

Names that start exactly the same thing are one entry, named by all of them in RetroDECK's order: RetroDECK lists the
same RetroArch core for PlayStation 2 as LRPS2 and as PCSX2, so the menu offers **LRPS2 / PCSX2** once, and the header,
a game's Emulator column and the line under the button while it switches name it the same way, as does the toast after a
game's own switch. One emulator started in different ways stays one entry per way — RPCS3 by game serial is one, RPCS3
from a folder or an ISO (one command under two names) another. Picking a joined entry stores its first name; a choice
stored under either name before reads as that entry.

1. Open **Library** from the main QAM page and move to the **Platforms** tab
2. Move to the platform you want to change
3. Press the chip button and pick an emulator from the menu
4. The BIOS table below updates to show what the new choice needs

A platform that offers one emulator says so instead of showing a button. A platform whose emulators Tender cannot pin —
they may need setting up, or ES-DE's command for them may not be one Tender can bake — says that too, and RetroDECK
picks one itself when a game starts. Where the emulator RetroDECK would have fallen back to is **not installed**, the
pane names it and says nothing here can pin a different one; those games will not start until it is installed. And a
platform RetroDECK lists **no** emulator for says so, which is a different thing again: there is nothing for RetroDECK
to fall back to at all. If the emulator list itself cannot be established, the pane says why rather than showing an
empty picker: no emulator source was found, every source is switched off in Settings › Emulator sources, ES-DE's systems
file is broken, the source's list cannot be read yet (EmuDeck's today), or it is not established for another reason.

Tender stores the choice in its own settings and **immediately re-applies it** to every installed game on that platform
— the change takes effect right away, with no sync needed (games that already have a per-game core keep their own
choice). If the switch cannot be made, the pane says so under the button and the header keeps naming the core that is
actually in effect. Once a sync has run, the page works even when your RomM server is offline — core switching and BIOS
status are available, only the download buttons are withdrawn. Before the first sync after an update, a platform needs
RomM once to learn its system; offline, it says so instead.

!!! note "A RetroDECK default-core change needs a Force Full Sync"

    Setting a per-platform core here re-bakes your installed games right away. But if a **RetroDECK
    update** ships a _new default core_ for a platform (and you have not picked a core yourself), that new default does
    **not** take effect on a normal sync — a normal sync skips platforms whose games haven't changed, so the
    previously-baked core stays. Run a **Force Full Sync** to re-bake every game and pick up RetroDECK's new default.

### Per-Game (Game Detail Page)

On the game detail page, a **CPU button** (microchip icon) appears between the RomM and Steam gear buttons when the
game's platform offers more than one emulator. It is there too when Tender cannot get the emulator list, and its menu
then says why — the same reasons the platform pane gives. The menu lists the same emulators as the platform pane —
RetroArch cores and **standalone emulators** — and shows the ones Tender can't launch from Steam as **disabled** with a
short reason. Names that start the same thing are one entry here too (**LRPS2 / PCSX2**), and the markers described
below go on that entry whichever of its names was picked.

1. Open a game's detail page
2. Tap the **CPU button** (microchip icon)
3. Pick an emulator from the menu, or the **Use System Override** item at the top (see below)
4. The BIOS status, core badge, and game info panel update immediately

At the top of the menu, above the core list, is a dedicated **Use System Override (X)** item. Selecting it **clears**
the per-game core so the game follows whatever the system would pick — the per-platform core you set in Library ›
Platforms, or the platform's default core when no per-platform override is set. **X** is that fallback core's name,
shown in parentheses so you know what the game will fall back to.

Each core in the list below can show up to three markers, one per role:

- **(default)** — the RetroDECK/es_systems default core for this platform.
- **(system)** — the per-platform core you picked in [Library › Platforms](#per-platform-library-platforms-tab) (stored
  in Tender's settings). Absent when the platform has no per-platform override.
- **✓** (checkmark) — the core this game actually launches with right now.

The three roles are independent, so a single core can carry more than one marker: "(default) (system)" when your
per-platform pick happens to equal the default, or "(system) ✓" when the per-platform core is also the one the game
launches with.

A per-game core takes priority over the platform default. **Every core in the list pins** when you pick it — including
the one marked **(default)**. Pinning the default-marked core fixes the game to that specific core even if you later
change the per-platform override; it is no longer the way to "follow the system".

To drop the per-game core and follow the platform/system core again, pick the **Use System Override** item at the top —
that is the only thing that clears the per-game override. The **✓** can appear in two places at once: when the game is
following the system (no per-game core), the **Use System Override** item carries the ✓ **and** so does the core that is
actually in effect. When you pin a per-game core, only that pinned core carries the ✓ and the **Use System Override**
item does not.

When you set or reset a per-game core for an installed game, Tender updates the game's Steam shortcut immediately and
confirms the change landed before reporting success. If Steam can't accept the change in the current session, you'll see
a "Core saved — restart Steam to apply" message — your choice is still saved; it takes effect after a Steam restart (or
the next sync).

Per-game cores work for **any ROM filename**. Tender bakes the chosen core directly into the game's launch command, so
it does not rely on RetroDECK's gamelist lookup (which mishandles parentheses and other special characters in filenames)
and is not affected by that upstream limitation.

### Core choices are not migrated from ES-DE

Tender now owns core selection entirely and no longer reads or writes ES-DE's `gamelist.xml`. A few notes for anyone
upgrading from an older build or who edits ES-DE directly:

- **Per-platform cores set in ES-DE are not carried over — re-apply them once.** Earlier builds stored a per-system core
  as a `<alternativeEmulator>` in ES-DE's `gamelist.xml`; Tender now stores per-platform cores in its own settings and
  does **not** read or import that ES-DE entry. If you had set a per-system core, re-apply it once in **Library ›
  Platforms** (the Emulator Core button/menu) and it sticks from then on.
- **Per-game cores set with an older version of Tender are not carried over.** Earlier builds stored per-game cores in
  ES-DE's `gamelist.xml`; Tender now stores them itself and does not import the old entries. Re-apply any per-game core
  once through the CPU-button menu and it sticks from then on (including across uninstall/re-download).
- **A core set directly in ES-DE is not seen by Tender.** If you pick a core for a game (or a system) in ES-DE's own
  interface, Tender's BIOS badge, per-core save path, and core-change warning will **not** reflect it — those follow the
  core Tender knows about, and Tender's launches always use the core it has baked in. ES-DE-native launches still honour
  your ES-DE setting. To keep Tender's badges, save paths, and launches in sync, set the core through Tender (the
  CPU-button menu for one game, Library › Platforms for a whole platform) instead.
- **A custom system definition IS seen.** If you have added your own
  `<RetroDECK home>/ES-DE/custom_systems/es_systems.xml`, Tender now reads it the way ES-DE does: a system you redefine
  there replaces the shipped one entirely, and a `<loadExclusive/>` in that file makes it the whole list. So the
  emulators the picker offers, and the default it marks, can differ from what an older version of Tender showed for a
  system you customised. That is deliberate — Tender's list should match the emulators your ES-DE actually has.

### Non-Default Core Indicator

The CPU button changes color to indicate the active core status:

- **Gray** — the default core is active (no overrides)
- **Yellow** — a non-default core is active (per-game or per-platform override)

The game detail info panel shows the emulator this game launches with in a dedicated "Emulator" column alongside the
BIOS status, using a two-column layout. The column heading is its only label — the emulator's name stands under it on
its own, the way the BIOS column's readiness line stands under "BIOS", and it reads the same whether what launches the
game is a RetroArch core or a standalone emulator.

---

**Previous:** [Managing Games](managing-games.md) | **Next:** [RetroDECK Path Migration](retrodeck-path-migration.md)
