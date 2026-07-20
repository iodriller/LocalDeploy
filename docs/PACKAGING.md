# Desktop packaging

Experimental. Both builds wrap the existing FastAPI backend + web UI behind a
system tray / menu-bar icon (`packaging/tray_app.py`) - no separate installer
logic to maintain, no Python or virtualenv required by the end user.

## Windows: `.exe` and `.msi`

```powershell
python -m pip install -e ".[packaging]"
.\packaging\build.ps1          # dist\LocalDeploy\LocalDeploy.exe (a folder)
.\packaging\build.ps1 -Msi     # also dist\LocalDeploy-Setup.msi (double-click installer)
```

`-Msi` fetches the WiX v3.14 toolset itself on first run (a plain NuGet
package into `build\wix-tools`, no admin rights, no license fee - WiX v6/v7
gate every command behind a paid "Open Source Maintenance Fee" EULA above
$10k/year project revenue; v3.14 predates that entirely). Verified on this
machine: clean per-user install (no UAC prompt), Start Menu + Desktop
shortcuts, a working entry in **Settings > Apps** with a working Uninstall,
and a fully clean uninstall (files, shortcuts, and registry all removed).

Source lives in `packaging/windows/`:
- `product.wxs` - the installer definition (install dir, shortcuts, upgrade
  behavior, the Add/Remove Programs entry).
- `fix_perfile_keypaths.py` - a required post-processing step; see its own
  docstring for why (short version: Windows Installer requires every
  component in a per-user install to use an HKCU registry key as its
  detection anchor, not a file, and the file-harvesting tool doesn't know
  that on its own).
- `license.rtf`, `localdeploy.ico` - installer wizard assets.
- `files.wxs` is *generated* (by `heat.exe`, from whatever's in
  `dist\LocalDeploy` at build time) - never hand-edit it, it's overwritten
  every build and is not committed.

**Unsigned**: the `.exe` and `.msi` are not code-signed, so Windows
SmartScreen will show "Windows protected your PC" the first time someone runs
either one. The workaround (More info -> Run anyway) is safe here but looks
alarming to someone who doesn't already trust the publisher - see
[Code signing](#code-signing-not-done) below.

## macOS: `.app` and `.dmg`

```bash
pip install -e ".[packaging]"
pyinstaller packaging/macos.spec --distpath dist --workpath build
```

Produces `dist/LocalDeploy.app` - a menu-bar-only utility (`LSUIElement=1` in
its Info.plist: no Dock icon, no Cmd+Tab entry, matching the Windows build's
tray-only behavior). Building the `.dmg` from that is one `hdiutil` call; see
`.github/workflows/build-macos.yml` for the exact steps (staging folder with
the `.app` plus an `/Applications` symlink, then `hdiutil create -format
UDZO`).

**Important caveat**: this spec and workflow were authored without access to
a Mac (built from a Windows machine, reasoning from PyInstaller's documented
`BUNDLE()` API and macOS's documented `hdiutil`). The GitHub Actions workflow
includes an automated smoke test (launches the built app, waits for
`/health`), but **that workflow has not actually been run yet** - it's
`workflow_dispatch` (manual trigger) on purpose, so it doesn't fire
automatically and cost Actions minutes until someone runs it deliberately.
Before trusting the `.dmg` for real: run the workflow (Actions tab, or `gh
workflow run build-macos.yml`) and open the resulting `.app` on a real Mac at
least once. It also currently targets Apple Silicon only (`macos-latest`
runners are arm64); an Intel/universal2 build would need extra work this
hasn't had.

**Unsigned + unnotarized**: Gatekeeper will refuse to open it at all via a
normal double-click ("LocalDeploy.app is damaged and can't be opened" or
similar) until the user right-clicks -> Open once, or clears the quarantine
attribute (`xattr -dr com.apple.quarantine LocalDeploy.app`). This is a
harder wall than Windows SmartScreen's "Run anyway" - macOS notarization
(below) is the only way to remove it entirely.

## Code signing: not done

Neither build is signed. What each platform actually requires:

- **Windows**: an OV or EV code-signing certificate (~$100-500/year from a
  CA), used to `signtool sign` the `.exe` and `.msi`. [SignPath.io](https://signpath.io/)
  offers free signing for qualifying open-source projects, which is the usual
  path for a project like this rather than buying a certificate directly.
- **macOS**: an Apple Developer Program membership ($99/year, mandatory - there
  is no free tier for notarization), used to sign the `.app` with
  `codesign` and submit it to Apple's notary service (`xcrun notarytool`)
  before stapling the notarization ticket. Without this, Gatekeeper's
  "damaged" message is not optional to remove.

Both are real costs and identity/payment decisions that only the project
owner can make - not something to set up unilaterally. Until one or both
happen, both installers work correctly; they just require the one-time
"trust this anyway" step described above on first run.
