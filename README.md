# Wabbajack Viewer

Browse a `.wabbajack` and compare it with an installed Amethyst or Mod Organizer 2 profile. Comparison reads your files without changing them.

Comparison, file-content verification, profile-file differences and HTML/JSON reports support both managers. **Create profile copy supports Amethyst only, on Linux. MO2 profile copying is not supported.** Wabbajack browsing, snapshots, mod-order exports and separator exports are independent of the installed manager.

## Run

```bash
./launch.sh
# or
python3 wabbajack_viewer.py
```

Python 3.9+ with tkinter is required. Optional content verification uses the system `libxxhash` library. Run from source or build a fresh executable with PyInstaller.

For a clean testing launch, close any running viewer windows, then run:

```bash
./launch.sh --clean
# or
python3 wabbajack_viewer.py --clean
```

`--clean` deletes the automatic session, all viewer/comparison/profile-copy preferences (including column widths and pane layout), and the file-hash cache before opening the app with defaults. Manually saved snapshots, exports, installed profiles and legacy Workbench records are kept. The app saves new state normally; repeat `--clean` whenever you want to start fresh.

## View and compare mods

1. **Open Wabbajack…** (Ctrl+O). The file picker starts in your Downloads folder, including custom Linux XDG and redirected Windows Downloads locations. Choose **Wabbajack profile** if it contains several profiles.
2. In **Mods**, select an installed profile. **Settings…** opens **Profiles folder**, the detected mods folder and the optional content check in a separate window. Amethyst's usual location is detected automatically. You can select a particular profile directory in the folder chooser too.
3. Click **Compare**. Both sides appear in the same table, with their names, versions, enablement and positions in mod order.

| Color | Meaning |
| --- | --- |
| Green | Recorded metadata, relative order and enablement match. |
| Amber | A version, source, identity, order, enablement or content differs, or evidence needs review. |
| Red | In the Wabbajack profile, absent from the installed profile. |
| Blue | Listed in the installed profile, without a match in the Wabbajack profile. |

Installed-only rows appear automatically beside nearby matched mods. The explicit **Result** column gives the meaning without relying on color. Search and filter the combined list, or click column headings to sort. Sorting and comparison never change authored mod-order exports.

Mod Name uses spare table width until you manually resize or fit it. Other column widths stay fixed when you resize the window. Manual widths are saved across filtering, comparisons, view changes and restarts. Defaults fit headings and available content without stretching blank columns. Right-click a column heading for **Fit columns to contents** or **Reset column widths** (restores automatic Mod Name expansion), or double-click a divider to fit one column. Active states consistently use **Yes**, **No** and **—** for an absent side.

Select a mod for **Comparison** evidence, including Nexus IDs, versions, source archives, exact folder paths, relative order changes and file verification results. **Open installed mod folder** opens its files and `meta.ini`. **Overview** identifies the recorded mod archive and the download archives Wabbajack uses. **Install Instructions** groups operations by archive; **Output Files** shows the expected destination tree, sizes and recorded hashes. Both use **Directive type** consistently. Select a file and choose **File details…**, or double-click it, for an explanation and the complete raw directive JSON, including patch and transformation settings. Right-click a mod for source pages, copying and supported extraction-script exports; Ctrl+C copies displayed table columns.

An archive may supply an identical file that also exists in another download. Its appearance does not establish a mod dependency. Additional archives supplying only known empty files are labelled **Empty files only**, with their file count and zero-byte contribution, and collapsed by default in Install Instructions. Searching expands matching groups. The original extraction mappings are preserved; the viewer does not assume an alternative archive contains the same file without inspecting it. Generated files with unknown sizes are not classified as empty files.

The **Archives** column excludes embedded package files. Those remain visible as separate groups in Overview and Install Instructions. If an archive cannot be inspected, Overview marks source verification as incomplete rather than implying that its recorded extra sources have been confirmed necessary.

Source contributions describe the destination files: configuration files, male or female animation clips, and sprint animations when applicable. Mixed groups retain their total file count with a breakdown. These descriptions come from destination paths, not archive titles. Additional archives are labelled **Recorded extraction source**. Overview identifies titles as recorded metadata and shows the actual archive filename; Install Instructions has separate recorded-title and archive-filename columns. Extraction references do not establish mod dependencies.

Use **Downloads folder…** in Install Instructions to locate the downloaded archives. Opening a Wabbajack verifies preferred sources during loading, before publishing the full mod list. This phase runs in the background with progress and cancellation; the previous package remains visible until the new one is ready. Plain `FromArchive` files, including non-empty files, can use the mod's own archive for presentation only after its archive hash and the member's corresponding path, byte size and output hash are verified. Prefixes such as `Data/` are allowed when established by the mod's other mappings. Patched, transformed and generated files retain their recorded instructions. Archive listings and hashes are reused within the loading phase. Snapshots retain the results, while profile changes check any uncached sources. Changing the downloads folder checks the current list in the background. Verified files appear under the mod's own archive in Overview, Install Instructions and Output Files; incidental archives disappear when no displayed files use them. File details retain the recorded archive and member, and raw directives and extraction-script exports keep Wabbajack's original mappings. Missing downloads, unavailable verification tools, ambiguous paths or changed archives leave the recorded sources in place. ZIP inspection uses Python; other archives require 7-Zip, and archive hashing uses the system xxHash library. The downloads-folder preference is remembered, and restored verification results are checked against the archive's current file identity.

**Check mod file contents** optionally verifies matched folders against deterministic Wabbajack hashes, detecting changed, missing and extra files. It may read a large amount of data. Generated BSAs, remapped configurations, transformed textures and unsupported instructions remain unverified. Matching metadata alone does not establish byte equality. The status explains whether content was checked; uncertain content is never green.

**Profile file differences…** shows text differences for `modlist.txt`, plugins/load order and configuration. Amethyst's `ini files` folder is recognized. Compiler backups and crash logs are excluded from this active-settings view. Text previews are bounded to 512 KiB; generated/remapped files remain unverified. **Export report…** saves all mod results and profile-file differences as HTML or JSON, regardless of the active filter.

The detected **Mods folder** is shown directly. Normally no path setup is needed: the app reads Amethyst's `profile_state.json` or MO2's `ModOrganizer.ini`. **Choose mods folder…** handles an unusual layout or a Windows path accessed from Linux. **Use detected folder** restores automatic detection; changing the installed source clears the previous override. Private/shared mod stores and Wabbajack mod stores are supported.

MO2 profiles must be selected manually; automatic initial discovery looks for Amethyst installations. MO2's standard `instance/profiles/<profile>` layout and configured base/mods paths are recognized. If the profiles folder is stored elsewhere, use **Choose mods folder…** in the GUI or `--instance` / `--mods-dir` on the command line. Windows drive paths accessed from Linux also require choosing the corresponding local mods folder. These checks inspect the saved profile and mod files; they do not inspect a running manager's virtual filesystem or resolve in-game conflicts.

Mod matching first uses the folder name, then reciprocal unique Nexus mod/file IDs, source archive, version and mod ID. Multiple optional files with the same identity remain ambiguous. A version string alone never pairs unrelated mods. Versions are compared as recorded; newer/older is not inferred. Disabled entries are included; separators and unmanaged game/DLC entries do not participate in identity or relative-order comparison. Positions include their original place in the complete mod order.

Changing either source or comparison options clears old results. Background opening/comparison supports cancellation; an old worker cannot replace a newer comparison. A Wabbajack without an authored mod profile shows **Downloads**, with installed comparison disabled.

The compact header shows the package name and version. The mod list starts with 70% of the available pane space; existing saved divider positions are preserved. Drag the divider to adjust the space, or use **Hide details** / **Show details** to browse with the full list height. The divider and collapsed state are remembered. Details show the selected mod name, author/version/activity summary and copyable installation archive; folder and archive identities appear when distinct. Resizing reserves room for the instruction controls and file rows. **Find mods** includes a result count, a **×** button when a query is present, and Escape to clear the query. Search and installed-profile comparison share a toolbar at wide sizes and wrap at narrower sizes. **Hide details** sits beside the selected mod heading; **Show details** appears above the list while collapsed. Horizontal scrolling appears only when the mod columns overflow. Report actions appear after comparison results are available. The light palette uses white inputs and data surfaces, flat buttons, neutral section rows and underlined active tabs; enabled, disabled and focused controls have distinct states.

## Create a standalone profile

The second main tab is **Create profile copy**. Select the Amethyst **Profiles folder**, **Source profile** and **New profile name**. The source can be an installed Wabbajack profile or an ordinary Amethyst profile. The new name must be unique, including case variants. Existing files, folders and broken symlinks cannot be used as a destination. Invalid names are shown immediately and disable Preview.

The app reads `profile_state.json` and Amethyst's usual configuration automatically. If the profile specifies a separate game/root folder, **New game installation copy** appears. It means the game installation containing the executable and game data. The default destination is a sibling of Amethyst's staging folder; **Choose parent…** or the path field changes it. HexRim uses a separate root. A profile such as `default` uses Amethyst's shared game folder, so no root destination is shown and the game is not copied.

**Set up copy** uses the full panel for the form, including selectable detected source paths and the new profile destination. **Preview copy** switches to **Review copy** in the same space, showing destinations, file count, logical size and any limitations without writing files. **Back** preserves all form values and requires a fresh preview before creation. The Create action appears only in review. **Create standalone profile** revalidates the preview, creates independent mod/profile/tool files, and copies a separate game root only when used by the source. It rebases configuration paths, removes the Wabbajack installation association, preserves mod order and leaves the result undeployed. Existing names and destinations are checked again before publishing. Failed copying rolls back its own output, including a root that was already copied, without changing an existing destination.

Close Amethyst and restore/undeploy the source before creating a copy. Copying currently requires Linux. The visible options are **Copy saves** and **Also copy external custom tool folders**. Source game/config paths, original Windows path adjustments and copy mode are automatic. The command-line cloner retains overrides for unusual layouts; the normal UI does not require these technical settings.

Copy mode controls how independent files are created. Automatic mode tries filesystem copy-on-write (a reflink) and falls back to ordinary copying. A reflink initially shares unchanged disk blocks, while edits to either file stay independent. Both methods create separate files; hard links are not used.

This creates a standalone **Amethyst profile from an installed Wabbajack profile**. It does not compile a new distributable `.wabbajack` installer.

## Snapshots and automatic restore

- **Export** saves the authored mod order or separator folders. Optional extraction scripts support simple archive/inline instructions; mods needing patches, nested archives or generated outputs require Wabbajack.
- **Snapshots → Save snapshot…** saves the parsed mod list, raw directives, selected profile and verified source choices in a compressed JSON file. **Open snapshot…** also accepts older saved-view files.

The app automatically saves the last session after opening a package, changing its profile, completing source checks and closing normally. On startup it restores that session, including search, sorting, selected mod and details tab. Initial loading shows a single progress panel with Cancel, using the last package name when known, and reveals the workspace when finished. Opening another package keeps the existing workspace visible during loading. Verified source choices are retained, so unchanged archives do not need another scan. Saves replace the previous snapshot atomically; failed or interrupted writes leave the last good copy available. The automatic snapshot is `wabbajack_session.wvmodlist.json.gz` beside the viewer's preferences file and is excluded from version control.

Opening packages, restoring snapshots, switching Wabbajack profiles and checking archive sources run in the background. An **Activity** panel shows a moving progress bar, current phase, elapsed time and **Cancel**. Cancelling an opening or profile switch retains the previous list. Archive-source cancellation retains completed checks. Manual snapshot saves and autosaves also run in the background, with visible saving feedback. Repeated autosaves keep the latest pending state; explicitly requested snapshots are retained. Normal closing waits for the final save while keeping the window responsive.

Source matching also checks complete destination trees inside FOMOD option folders, even when that option's prefix has not appeared in another selected file. It uses the size and content hash to distinguish variants. When 7-Zip cannot read an archive, the viewer can use the installed `lsar`/`unar` tools instead. **Recheck archive sources** discards cached decisions and performs a fresh check, including earlier failures.

When the original Wabbajack is still present and unchanged, the app rebuilds its package context from cached JSON and metadata without reopening the archive; profile switching and installed comparison remain available. If the original is missing or changed, the snapshot opens for browsing and explains the comparison restriction. Changed download archives invalidate their cached preferred sources. **Snapshots → Recheck archive sources** performs a fresh check. Installed comparison results are recalculated with Compare rather than restored as current evidence.

**Snapshots → Restore last session on startup** controls automatic restoration. With it off, the app starts empty while continuing to save a session for later use. If no automatic snapshot exists, the last manually opened saved view is used when available. Corrupt automatic snapshots do not cause a blocking startup dialog.

Previously recovered Workbench records and snapshots remain untouched in `~/.local/share/WabbajackViewer/workbench/` (or the configured XDG data folder). The legacy review/recovery subsystem is no longer included. Both GUI and command-line profile copying create independent profiles without review projects or baseline hashing.

## Command-line comparison

```bash
python profile_compare.py /path/to/list.wabbajack /path/to/profiles/Personal \
  --profile "Authored profile" --output comparison.html
```

Omit `--profile` for a Wabbajack with one profile. Options include `--mods-dir`, `--instance`, `--verify-files` and `--output comparison.json`. JSON retains its existing schema field names for compatibility.

## Verification

```bash
python -B -m unittest discover -s . -p 'test_*.py' -v
```

Tests use temporary Wabbajack/Amethyst/MO2 fixtures. They cover parsing, ambiguous identities, versions, mod order, content hashes, profile text differences, report escaping, the combined table, cancellation/stale results, independent copying, destination protection and rollback. UI tests need a display; hash tests need libxxhash. No real profile is created by the tests.

Column persistence, consistent active labels, conditional root copying and late destination-collision rollback are covered by regression tests.

## Comparison progress and faster file checks

The comparison table starts with **WJ #** and **Installed #** together. **Result** includes the matching basis (folder name, Nexus mod/file ID, archive, or version), while colors retain their difference meanings. A moving progress bar and phase/count messages remain visible during comparison; **Cancel** stops indexing, scanning and hashing at the next checkpoint.

File checks reject known size mismatches without reading the whole file and cache deterministic xxHash64 results in the user cache directory (`~/.cache/WabbajackViewer/xxhash64-v1.sqlite` on standard Linux setups). Cached hashes are reused only while device, inode, byte size, modification time and change time match. Edits invalidate the cache, including same-size edits with a restored modification time. Cache failures fall back to byte reads; partial/cancelled hashes are never stored. Results show bytes read and cached hashes reused. An initial check still reads all eligible unchanged-size files; generated/non-deterministic files remain unverified. The cache is disposable: removing it while the app is closed forces fresh byte reads next time.

**New game installation copy** means a copy of the list's Game Root, containing the game executable and Data folder. It may use a downgraded Skyrim version required by the mod list. Its copy is separate from Steam's installation, and is offered only when the selected profile has a separate root.

A 64 MiB local fixture required 64 MiB of reads on the first check and zero reads on the cached repeat; this is a cache behavior check, not a full-modlist speed benchmark. Session tests also cover responsive restoration, cancellation, queued snapshot saves and saving the latest state before closing.
