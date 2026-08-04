# CI Build & Release Notes

[中文](CI_zh.md) | English

The three-platform builds are automated by GitHub Actions. **Completely free for public repositories.**

## Triggers

- Pushing a `v*` tag (e.g. `v2.0-beta.1`) → builds all three platforms and publishes a Release
- Manual: Actions page → Build PrintTheShot Beta → Run workflow

```bash
git tag v2.0-beta.2 && git push origin v2.0-beta.2
```

## Pipeline (`.github/workflows/build.yml`)

```
build (matrix: ubuntu/macos/windows)
  ├─ checkout + setup-python 3.11
  ├─ run the platform's scripts/build_*.sh / build_windows.bat
  └─ upload artifact (missing files → job fails)
release (needs: build, tags only)
  ├─ download the three platform artifacts
  ├─ rename explicitly:
  │    PrintTheShot-linux          (Linux raw binary)
  │    PrintTheShot-macos.zip      (macOS .app archive)
  │    PrintTheShot-windows-x64.exe(Windows)
  └─ publish to GitHub Release
```

## Pitfalls (all encountered for real)

### 1. Windows: bare `pip` cannot upgrade itself inside a venv

**Symptom**: `pip install --upgrade pip` fails with `ERROR: To modify pip, please run the following command: ... python -m pip`.

**Cause**: on Windows, the venv's `pip.exe` cannot modify itself (file lock).

**Fix**: always use `python -m pip install ...` in the bat.

### 2. Windows: bat without error checks → "false success"

**Symptom**: pip install fails, but the bat keeps going and the last `echo` returns 0 — the job shows ✓ while the artifact is empty.

**Fix**: check `errorlevel` after every step and verify the artifact exists:

```bat
python -m pip install --quiet -r scripts\requirements.txt pyinstaller
if errorlevel 1 exit /b 1
python -m PyInstaller scripts\print_the_shot.spec --noconfirm
if errorlevel 1 exit /b 1
if not exist dist\PrintTheShot.exe (echo ERROR & exit /b 1)
```

> General rule: build scripts must surface failures explicitly — never silently return 0.

### 3. Release: multiple jobs publishing same-named files overwrite each other

**Symptom**: all three platforms produce a binary named `PrintTheShot` (PyInstaller default); each job calls `softprops/action-gh-release` independently, later runs overwrite earlier ones, and the Release ends up with one file.

**Fix**: build jobs do **not** publish; a separate `release` job depends on all builds, downloads artifacts, **renames explicitly**, then publishes (see pipeline above).

### 4. Release: `.app` directory flattened, leaking `Info.plist`

**Symptom**: with `download-artifact` + `merge-multiple: true`, files inside `PrintTheShot.app/Contents/` (Info.plist, MacOS/PrintTheShot) get published as standalone assets.

**Fix**:
- The macOS build script zips the `.app` (`zip -rq PrintTheShot-macos.zip PrintTheShot.app`) — the standard distribution form for macOS apps
- The release job does **not** use `merge-multiple`; it copies from each artifact directory precisely

### 5. Upload artifacts with `if-no-files-found: error`

Missing artifacts mean the build failed — the job must turn red, not silently skip.

## Local Builds (debugging)

| Platform | Command | Output |
|---|---|---|
| macOS | `./scripts/build_macos.sh` | `dist/PrintTheShot` + `dist/PrintTheShot.app` + `dist/PrintTheShot-macos.zip` |
| Linux | `./scripts/build_linux.sh` | `dist/PrintTheShot` |
| Windows | `scripts\build_windows.bat` | `dist\PrintTheShot.exe` |

Notes:

- PyInstaller **cannot cross-compile**: build on the target platform (the CI matrix does this natively)
- The spec (`scripts/print_the_shot.spec`) bundles `fonts/` `web/` `plugin/` as datas — **these directories must be committed to git**, or CI builds will fail
- Packaged builds read fonts/templates from `_MEIPASS`; the plugin is copied to CWD/plugin (writable, so updates work)

## Post-Release Checklist

1. Actions page: 3 build jobs + 1 release job all ✓
2. Release has exactly 3 assets with correct names (linux / macos.zip / windows.exe)
3. Spot-check: download the package for your platform, run `--render sample.json out.png` to verify chart rendering
4. macOS: unzip → `PrintTheShot.app` launches by double-click
