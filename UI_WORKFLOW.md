# UI workflow

Updated 2026-11-21 for responsive loading, compact controls and session restoration.

## Primary views

- **Mods**: one shared Wabbajack/profile selection, installed profiles-folder/profile selection, one combined colored mod table, search/filter/sort and selected-mod evidence/sources/files.
- **Create profile copy**: Amethyst profiles-folder/source selection, unique new name and, for a profile with a separate game root, a new root folder; preview then explicit creation. No review-project or baseline decisions.

There is one `.wabbajack` loader. **Compare installed** is part of Mods. The Tools menu, full-package inspector and names/order text-list comparison are removed, including merged text-list exports, visibility controls and sort locking. **Review updates** is removed from the active UI; previously saved records are preserved.

## Comparison meanings

Green = recorded metadata, relative order and enablement match. Content checking is separately explicit.
Amber = differences or incomplete evidence, including generated content that cannot be verified.
Red = Wabbajack only. Blue = installed only. Uncertain identity variants are amber rather than automatically paired.

Both names, versions, active states and mod-order positions are accessible in the table; narrow windows can scroll horizontally. Details show exact IDs, archive evidence, folder paths and file findings. The installed-folder action is enabled only for a present folder. Profile file differences include modlist, plugins, load order and INI text. Reports contain all rows, independent of display filters.

Comparison, content verification, profile-file differences and reports support Amethyst and MO2. Initial profile discovery is Amethyst-specific; MO2 profiles are selected manually. MO2 custom profile locations or Windows paths accessed from Linux may require **Choose mods folder…**. Copying is Amethyst-only and currently requires Linux; MO2 profile copying is unsupported.

## State and action consistency

- Wabbajack/model/profile changes cancel and isolate old comparison workers and clear displayed results.
- Installed profile/mods/instance/verification changes clear stale results.
- Main table browsing never mutates authored rows/order; local-only rows are projections for display.
- Clear comparison clears the result table overlay, report actions and evidence.
- Snapshots retain parsed package context and verified sources. An unchanged original Wabbajack enables profile switching and installed comparison without reparsing the archive; missing or changed originals allow browsing only.
- No authored mod profile means Downloads mode; installed comparison disabled.
- Comparison inputs are frozen while reading. Cancel appears only for an active comparison.
- Wabbajack loading and profile-copy work block conflicting actions; normal close waits for a copy/preview to finish safely.
- Copy options invalidate the preview. Create is disabled until a valid preview exists.
- Existing profile names and game destinations are rejected; source changes between preview and creation require another preview.
- Actual writes happen only after Create standalone profile. Preview and all comparisons are read-only.

## Layout and activity

- Comparison setup opens in a separate Settings window, leaving profile selection and Compare beside the table. Report actions appear only when results exist.
- Find mods has a live result count, Clear search and Escape. Overview is selectable but read-only. Install Instructions and Output Files consistently use Directive type.
- The mod/details divider is adjustable and remembered. Resizing reserves room for instruction controls and file rows. Copy settings scroll independently, leaving preview/create and progress visible.
- Package opening, snapshot restoration and authored-profile switching stage their data in a worker before publishing it. Cancelling retains the previous list and profile.
- Archive-source rechecks expose progress and cancellation and retain completed results. A shared Activity panel shows the phase, animation and elapsed time.
- Automatic and named snapshot writes run in a serialized background queue. Repeated autosaves coalesce; named snapshot requests are preserved. Closing waits responsively for the final save. Atomic replacement retains the previous good snapshot if writing fails.
- A consistent light palette separates surfaces, borders, active actions and selections. Comparison meanings remain available through text as well as color.

## Copy discovery

Profiles are listed from the selected Amethyst profiles folder. `profile_state.json` supplies Wabbajack location, game path and private/shared storage. A missing private mods link can resolve through the recorded Wabbajack `root/mods`; the recorded Wabbajack `root/Game Root` supplies a missing game setting. Explicit saved game paths take precedence. The normal UI has no technical override fields. Amethyst configuration, saved source paths, Windows path rebasing and copy mode are resolved automatically; CLI overrides remain for unusual layouts.

The independent copy loses Wabbajack installation identity and owns its mod/profile files. A separate source game root is copied; a shared Amethyst game remains shared. It is an ordinary standalone Amethyst profile, not a newly compiled `.wabbajack` archive. Shared external applications are listed in the preview; copying their containing folders is optional.

## Column and control consistency (2026-11-20)

- Mod/source/file tables do not stretch columns when the window changes size.
- Defaults size headings and available content; empty columns keep only heading space.
- Separator drags save widths, retained through filtering, re-comparison, mode changes and restart.
- Right-click a heading to fit all columns, or double-click a separator to fit one.
- Both comparison active columns use Yes / No / —.
- Mods-folder detection is visible directly; Choose mods folder and Use detected folder replace Advanced paths. Overrides clear when switching installed sources.
- Copy names are validated immediately, before copying and before publication, including case differences and non-profile filesystem entries.
- New root folder appears only for a saved per-profile game root. Shared-game copies create only a profile and retain the shared game setting.
- Source game override, config directory, Windows roots and copy mode are removed from normal copy controls.
- Copy saves and external custom tools remain explicit user choices. Automatic copying uses independent reflinks where available, otherwise ordinary copies.

Regression coverage includes saved widths and resizing, consistent active labels, detected/chosen mods folders, conditional root fields, shared-game and empty-profile copying, and late-name collision rollback. Real HexRim/default inspection confirms the root field is shown/hidden respectively, without creating a real copy.

Validation: 111 tests pass. A native Tk separator drag from 128 to 194 pixels retains 194 pixels after window resizing and in saved preferences. User-requested fitting can expand beyond the conservative default content-width cap. Settings tests use temporary files.

## Comparison follow-up (2026-11-20)

WJ # and Installed # are the first two columns. Result includes the matching basis. The main comparison now uses a shared job controller independently of the legacy standalone view. Progress is indeterminate overall (work varies by file size), with phase/count messages and Cancel; checkpoints cover metadata/index loops, directory scans and hash chunks. Cancellation never publishes a partial report.

The copy field is labelled New game installation copy, with an explanation of Game Root and potentially downgraded Skyrim executables. File checks report bytes read and unchanged-file hash reuse; generated content remains unverified. Stat identity/change checks invalidate stale hashes, and cache faults fall back to reading bytes. Empty installed profiles compare successfully even without a mods directory. Regression coverage is 118 tests.
