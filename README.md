# MagCopy

**Ctrl+Shift+A** — drag a box over anything. It's on your clipboard. No preview, no dialog, no window.

**Ctrl+Shift+S** — drag a box and record it. Escape stops and opens the editor. Trim, crop, save. The GIF comes out as big and as sharp as 10 MB allows.

Windows and macOS. Same shortcuts, same editor, same files out the other end.

<p>
  <a href="https://github.com/magholmes/MagCopy/releases/latest/download/MagCopy-windows.zip">
    <img alt="Download MagCopy for Windows"
         src="https://img.shields.io/badge/download-MagCopy--windows.zip-5E6DEE?style=for-the-badge&labelColor=16171E&color=5E6DEE&logo=windows&logoColor=white"></a>
  &nbsp;
  <a href="https://github.com/magholmes/MagCopy/releases/latest/download/MagCopy-macos.dmg">
    <img alt="Download MagCopy for macOS"
         src="https://img.shields.io/badge/download-MagCopy--macos.dmg-342E38?style=for-the-badge&labelColor=16171E&color=342E38&logo=apple&logoColor=white"></a>
  &nbsp;
  <a href="https://github.com/magholmes/MagCopy/releases/latest">
    <img alt="Latest version"
         src="https://img.shields.io/github/v/release/magholmes/MagCopy?style=for-the-badge&labelColor=16171E&color=342E38&label=version"></a>
</p>

<p>
  <img alt="MagCopy on Windows" src="docs/window.png" width="49%">
  <img alt="MagCopy on macOS" src="docs/window-macos.png" width="49%">
</p>

*Windows left, macOS right. dusk, one of five palettes — night, ember, tide, paper.*

## Install

**Windows.** One file in the zip: `MagCopy-Setup.exe`. Open it. Per-user, no admin prompt, no questions to answer. Uninstalls from Add or remove programs. There's a portable `MagCopy.exe` on the releases page if you'd rather not install anything; Chrome blocks that one far more often.

**macOS.** Apple Silicon, 12.3 or newer.

```bash
curl -fsSL https://raw.githubusercontent.com/magholmes/MagCopy/main/install-macos.sh | bash
```

Puts it in `/Applications` and starts it. This isn't shorter for convenience's sake. macOS refuses to open an un-notarised app outright — no "open anyway" button, the way SmartScreen has one — and the quarantine mark that triggers the refusal is applied by the browser, not by the file. Nothing curl fetches carries it. Same app, same signature, different road.

Otherwise: download the .dmg, drag MagCopy across, then right-click → Open, and Open again.

Say yes to **Screen Recording**. It's the only permission MagCopy needs; the shortcuts go through Carbon's `RegisterEventHotKey`, which doesn't want Accessibility.

Refuse it and nothing breaks — you get an empty desktop, because that's what macOS returns instead of an error. If your first screenshot is a picture of nothing, that was it.

After an update you have to grant it again, and it fails in a way worth knowing in advance: MagCopy appears in the list, switched on, and is refused anyway. macOS keys the grant to the app's code signature, and without a Developer ID every build is signed afresh, so the entry you're looking at belongs to the version you replaced. Remove it with **−**, reopen, allow. One certificate would fix this and Gatekeeper together.

Lives in the menu bar. No Dock icon.

<details>
<summary><b>If a scanner calls it a virus</b></summary>

It's a false positive with three causes.

A one-file PyInstaller build carries a compressed Python runtime, writes it to a temp directory at startup and runs it from there. That is structurally a dropper, and enough real malware ships this way that scanners match the bootloader on sight. It's the biggest cause, and it's why the download is an installer wrapping an ordinary folder instead.

Nothing is signed, so Windows has no reputation for the file. Chrome words a reputation block as "virus detected", which is misleading — no scanner necessarily found anything.

And what MagCopy does reads as spyware to a behavioural engine: reads the screen, writes the clipboard, claims global hotkeys, adds an autostart entry, hides in the tray, launches bundled executables. All of that is the program. It's also the profile of an infostealer, and a heuristic can't tell them apart.

SmartScreen's "Windows protected your PC" is the same reputation problem: More info → Run anyway. If you'd rather trust nothing you can't read, run it from source.

</details>

## The shortcuts are yours

Those two are defaults. Click either one in the window and press what you want — `Ctrl+Space`, `Alt+F9`, `Win+Shift+3`, whatever's free. Takes effect immediately.

One rule: at least one modifier, because a bare key would swallow that key everywhere else. If something already owns the combination, MagCopy says so, keeps the one that was working, and tells you from the tray. A shortcut that silently does nothing is the worst way for this to fail.

## The GIF pipeline

This is the part with opinions in it.

**Record better than you need to.** Frames go from a reused GDI device context straight into x264 at CRF 14, **yuv444p**, no chroma subsampling — 4:2:0 is what smears text edges. Everything after this is subtraction, so the master is the one place not to throw anything away.

**Frame rate is chosen, not assumed.** A GIF stores each frame's delay in hundredths of a second, so only rates of 100/n play true. 30 fps becomes 3 cs and runs **11% fast**. MagCopy records at 50, 25 or 20 and never 30, and every rung of the ladder — 50, 25, 20, 16⅔, 12.5, 10 — is both an exact centisecond delay and an even decimation of the recording.

**Frame rate goes before pixels.** Screen text stops being readable long before motion stops reading as motion, so the ladder drops to 12.5 fps at full size rather than half size at 25. Camera footage wants the opposite; flip the loop order in `magcopy/optimize.py`.

**Measure, don't guess.** A scout pass encodes a few seconds from the middle and extrapolates by duration, which is enough to skip the rungs that can't fit. At the chosen rung it binary-searches gifski's quality for the largest file still under budget, then takes a free lossless `gifsicle -O3`.

**Fitting isn't optional.** If nothing on the ladder fits it keeps shrinking until something does. A GIF Discord refuses is not a result.

A 20-second 1280×720 screen recording lands at full resolution, 12.5 fps, about 9.4 MB, in a minute or two. Short clips take seconds.

You should usually send an MP4 instead — Discord autoplays them and they look better at the same size, because 256 colours costs more than the size limit does. MagCopy makes GIFs for when you specifically need a `.gif`.

## Borrowed from OBS

Scheduling fit to pace the frame loop, with a short spin at the end of each wait. Windows needs a 1 ms system timer for it; the default 15.6 ms granularity can't pace 25 fps at all and frames arrive in clumps. macOS sleeps accurately already and wants a quality-of-service class instead. A 4-second capture at 25 fps lands 101 frames and drifts 40 ms on both — the number that says the port is real rather than merely running.

Above-normal priority while recording, so an unrelated busy process can't shear the capture. And timing anchored to the wall clock rather than a frame counter: if a capture overruns its slot the loop repeats the previous frame instead of letting the recording slide out of sync with what happened. Repeats are counted and reported.

Not borrowed, on Windows: OBS captures through Windows Graphics Capture and DXGI desktop duplication, which is genuinely better and can see GPU-composited and fullscreen-exclusive windows that GDI returns as black. It also needs WinRT and D3D11 interop, well past what `ctypes` should be asked to do. **Fullscreen games are the known gap.**

macOS doesn't have that gap. ScreenCaptureKit sees everything, so the Mac build records games the Windows one returns black. It only sends a frame when the picture changed, which sounds like a problem for a paced loop and isn't — the same picture twice is the screen standing still, and the repeat accounting above already says the right thing about it. It can also leave MagCopy's own windows out of the stream.

## Choosing the region

Recording and screenshots pick a region differently, on purpose.

**Recording stays live.** You're about to capture motion, so nothing freezes: the screen keeps moving while you choose, and the selection sits at full brightness while everything around it dims. That's a hole cut out of the dim layer, not a lighter rectangle painted on it — a uniformly translucent window can't paint part of itself brighter. `SetWindowRgn` on Windows, a Core Animation mask on macOS.

The crosshair and readout live on a second layer above the dim so they aren't dimmed with it. Windows makes the rest of that layer vanish with a colour key; macOS, where Tk has no colour key at all, masks the layer down to exactly the shapes drawn on it — more exact, as it turns out, since it doesn't depend on no real pixel ever being the key colour.

**Screenshots freeze.** A menu or tooltip stays put while you frame it, and what you framed is what you get.

There's no preview, so the confirmation is the only sign it worked: the window says what it copied, and when the window is closed — most of the time — the tray says it instead. Without that, a capture that worked looks exactly like one that didn't.

You aim with the OS crosshair plus a short drawn cross at the pointer. Two earlier attempts aren't worth repeating. Full-screen guide lines cost **24 ms per mouse move**, because moving a line that long forces a screen-sized repaint and, on a layered window, a recomposite — visible lag just hovering around deciding where to drag. Thin always-on-top windows are fast, but Windows reports a one-pixel layered window as mapped while compositing nothing, so there was no pointer on screen at all. The short cross costs 0.08 ms and actually appears.

With a **shape** set the drag holds that ratio, including when it's clamped at the edge of the desktop — exactly where a naive implementation quietly bends it back out of shape. Shift overrides for one drag; with no shape set, shift means square.

## While recording

A viewfinder marks the region: a hairline edge saying where the boundary is, solid brackets at the corners, and a small bar with elapsed time and stop. The weight is in the corners, so the region reads as framed without a red box sitting on whatever you're recording. Every piece sits *outside* the captured rectangle and is click-through, so none of it lands in the GIF and none of it eats a click meant for the app underneath.

**Escape stops it** and takes you to the editor. So does the shortcut again, or the stop button. Escape has to be claimed as a real global hotkey to work at all — while recording, the focused window belongs to whatever is being recorded, so nothing of MagCopy's is in a position to see the key. It's registered when recording starts and released the instant it ends.

## Editing

The editor opens on its own, at most 80% of the monitor the clip came from, never upscaled past the recording's own size. It measures its controls while still hidden to work out how much room the picture gets, then maps once at that size, so nothing is ever seen to resize. Scrub the filmstrip, drag the handles to trim, set a speed, save. Space plays, Ctrl+S saves, Escape discards.

**Drag on the picture to crop**, then drag any edge or corner, or from inside to move the whole box. `save crop` locks it, `reset crop` undoes it. The crop applies to the full-quality master before anything is scaled, so cropping to the interesting part spends the entire size budget on it.

Saving takes a minute or more, so it says so: the button reads "saving", a bar tracks which size and frame rate the optimiser is trying, and a counter ticks the seconds beside it. ffmpeg can be quiet for half a minute at a time, and a status line that hasn't moved in that long is indistinguishable from a frozen program.

When it's written, the folder opens with the file selected and the editor asks **all done?** instead of closing itself. `done` closes it; `keep editing` leaves everything in place so you can adjust the trim or crop and save again. A GIF is often nearly right, and finding out means looking at it.

Files are named for the day — `2026-09-20.gif`, then `-2`, `-3`. The name you read should be the date, not a wall of digits.

Playback runs at the recorded rate, anchored to the wall clock rather than stepping a fixed number of milliseconds per tick, because every late tick is time the clip never gets back. Preview frames are extracted once as small JPEGs and paged in on demand; scrubbing a video file through a decoder is far too slow to feel like scrubbing. The trim is still expressed in seconds against the master, so nothing about the preview limits the output.

## Settings

In the window, and in `settings.json` beside the script — `%APPDATA%\MagCopy\` or `~/Library/Application Support/MagCopy/` when frozen.

| | |
|---|---|
| **theme** | dusk, night, ember, tide, paper — selection accent, crop handles, playhead and dim all follow it |
| **window size** | small → x-large |
| **recording** | 50 / 25 / 20 fps, cursor on or off, frame drawn or not |
| **shape** | free, or hold every selection to 1:1, 4:5, 5:4, 4:3, 16:9 |
| **max length** | 10 / 20 / 30 / 60 seconds |
| **size limit** | 8 / 10 / 25 / 50 MB — Discord gives 10 free, more with Nitro |
| **after** | copy the GIF to the clipboard as a file, show it in its folder, also save screenshots |
| **startup** | start with Windows or at login, straight to the tray |

The finished GIF goes on the clipboard as a *file*, so Ctrl+V in Discord attaches it rather than pasting a path.

Closing the window leaves MagCopy running; the shortcuts keep working. Quit from the tray menu or the `quit` link. Only one copy ever runs — launching it again brings the running one forward, since two copies can't both hold the same global shortcuts.

## From source

Python 3.9+, `numpy`, `Pillow`, and three external encoders. macOS also wants PyObjC and a Python whose Tk is 8.6 or newer; the system 3.9 ships 8.5, too old to draw this.

```bash
git clone https://github.com/magholmes/MagCopy.git
cd MagCopy
python -m pip install -r requirements.txt
python tools/fetch_binaries.py      # ffmpeg, gifski, gifsicle into ./bin
```

Then `magcopy.pyw` — double-click it, or `Launch MagCopy.bat` on Windows, `python magcopy.pyw` on a Mac. `requirements.txt` pulls the PyObjC pieces only on macOS.

On macOS `fetch_binaries.py` downloads ffmpeg and gifski and *builds* gifsicle, because nobody publishes a self-contained macOS binary of it. Needs the Xcode command line tools; `brew install gifsicle` works too.

No build step and no compiled extension on either platform. Windows is `ctypes` against Win32; macOS is PyObjC, with one small `ctypes` island for Carbon's hotkey API, which has no Objective-C face.

## Building

```powershell
python -m pip install pyinstaller
.\build.ps1 -Folder      # dist\MagCopy-Setup.exe, zipped as MagCopy-windows.zip
.\build.ps1              # dist\MagCopy.exe, one file
```

```bash
python -m pip install pyinstaller
python tools/make_icon.py icon_source.png --icns
./build_mac.sh           # dist/MagCopy.app, .zip and .dmg
```

The installer's payload is a folder build, which doesn't unpack itself at startup — the behaviour that makes scanners flag a one-file build. About 120 MB unpacked on macOS, of which ffmpeg and gifski are 96.

`build_mac.sh` does two things that aren't obvious. It ad-hoc signs the bundle every time, which isn't notarising but matters anyway, since macOS keys the Screen Recording grant to the signature and an unsigned build re-prompts on every rebuild. And it zips with `ditto` rather than `zip`, which keeps the symlinks inside a `.app` intact — a plain zip flattens them into something that won't launch.

## Look

Are.na's colour ladders and the layout language of the magnus archive site — hairlines, Geist and Geist Mono, lowercase mono labels, pill controls — in a frameless rounded window. Same as [Opmize](https://github.com/magholmes/Opmize), where the design came from. Rounded corners need Windows 11.

## Tests

```bash
python tools/run_all_tests.py
```

Twenty-nine of them, about three minutes, the same set on both platforms. They drive the real region selector, measure recording pace against the wall clock, hold an aspect ratio through every drag direction and the edge of the desktop, walk every theme and window size, size the editor against each monitor at each UI scale, run a real recording out the other end as a GIF, and read that GIF back by parsing its block structure rather than trusting the encoder.

Several sample the screen itself rather than the code's own idea of what it drew. That's how two bugs were found where Windows reported a window as mapped while compositing nothing, and it earned its keep again on the Mac, where the picker's chrome layer was quietly painting a black sheet over the whole selection while every test that asked the code what it had drawn was perfectly happy.

One reads every call into the platform layer with `ast` and checks the name exists on the platform running it and takes the arguments given. Two signatures drifting apart is how recording once failed completely on Windows while the exception was caught and reported as a polite message.

Two are macOS-only, for the two ways that build fails while looking like it works: a denied Screen Recording permission, which captures an empty desktop rather than raising; and MagCopy's own overlays landing in a recording — that one puts a magenta window *inside* the region and asserts no frame contains a magenta pixel.

The two that touch a real user setting — the Run key on Windows, the LaunchAgent on macOS — snapshot the raw value and put exactly that back. An earlier version keyed the restore off how the app interpreted the value, and once that became path-aware it deleted the user's own entry instead of restoring it.

## Known limits

- **Windows:** GDI can't see fullscreen-exclusive games or some GPU-composited surfaces; they come out black. macOS doesn't have this limit.
- **macOS:** the release is Apple Silicon, 12.3+. An Intel Mac needs one run of `build_mac.sh` on an Intel Mac — `fetch_binaries.py` already picks the right ffmpeg for the machine it's on.
- **macOS:** the editor preview can't be pixel-sharp on Retina. Tk draws one image pixel per *point*, so a full-resolution frame renders at twice the size rather than at twice the detail. Frames are extracted with lanczos and lightly sharpened, which recovers about 40% of what a plain bilinear downscale throws away, and that's all there is without leaving Tk. Preview only — the saved GIF comes from the full-resolution master.
- **macOS:** not notarised, so first launch is right-click → Open, and Screen Recording has to be granted again after every update. Both are the same missing certificate.
- A shortcut another application already owns can't be registered. MagCopy says so and keeps the one that was working.

## Checking an install

```powershell
MagCopy.exe --selftest
```

Reports whether the encoders, settings folder, screen capture, clipboard, fonts and hotkey parsing all work, and leaves the same report in `%APPDATA%\MagCopy\selftest.txt`.

## Licences

MagCopy is MIT. The tools it runs are separate programs, invoked as subprocesses, under their own:

| | |
|---|---|
| [ffmpeg](https://ffmpeg.org) | GPL (gyan.dev essentials on Windows, osxexperts static on macOS) |
| [gifski](https://gif.ski) | AGPL-3.0 |
| [gifsicle](https://www.lcdf.org/gifsicle/) | GPL-2.0 |
| [Geist / Geist Mono](https://vercel.com/font) | SIL Open Font Licence — `fonts/LICENSE-Geist-OFL.txt` |

None of the three are bundled here; `tools/fetch_binaries.py` pulls them from the projects' own releases and checks each one runs. The icon is cut from a photo the author owns.
