# MagCopy

Two shortcuts, no ceremony.

- **Ctrl+Shift+A** — drag a rectangle over anything. The screenshot goes straight to the clipboard. No preview, no save dialog, no window. Paste it wherever you were going.
- **Ctrl+Shift+S** — drag a rectangle and record it, up to 20 seconds. Trim it, then save. The GIF comes out as large and sharp as will fit under Discord's limit.

Both shortcuts are rebindable. Windows only.

![MagCopy](docs/window.png)

*dusk, one of five palettes — also night, ember, tide and paper.*

## Install

MagCopy needs Python 3.9+ with `numpy` and `Pillow`, and three external encoders.

```bash
git clone https://github.com/magnusholmes-cmyk/MagCopy.git
cd MagCopy
python -m pip install -r requirements.txt
python tools/fetch_binaries.py      # downloads ffmpeg, gifski and gifsicle into ./bin
```

Then double-click `magcopy.pyw`, or run `Launch MagCopy.bat`.

Everything else is `ctypes` against Win32, so there is no build step and no compiled extension to install.

Closing the window leaves MagCopy running — the shortcuts keep working and it sits in the notification area. Quit from the tray icon's right-click menu, or the `quit` link in the window.

## The GIF pipeline

This is the part with opinions in it.

**The recording is deliberately over-good.** Frames are captured with GDI `BitBlt` into a reused device context and piped straight to x264 at CRF 14 in **yuv444p**. No chroma subsampling: for screen content, 4:2:0 is what makes text edges smear. Everything after this is a reduction, so the master is the one place not to throw anything away.

**Frame rate is chosen, not assumed.** A GIF stores each frame's delay in hundredths of a second, so only rates of the form 100/n play back at true speed. 30 fps becomes 3 cs and runs **11% fast** — which is why MagCopy records at 25 or 50 fps, never 30, and why every rung of the quality ladder (50, 25, 20, 16⅔, 12.5, 10) is both an exact centisecond delay and an even decimation of the recording.

**The optimiser spends frame rate before it spends pixels.** Screen recordings fail differently from camera footage: text stops being readable long before motion stops reading as motion. So when something has to give, the ladder drops to 12.5 fps at full resolution rather than half resolution at 25 fps. (For camera footage the right call is the opposite — see the note below.)

**It measures instead of guessing.** A scout pass encodes a few contiguous seconds from the middle and extrapolates by duration, which is enough to skip the rungs that provably cannot fit. Then at the chosen rung it binary-searches gifski's quality for the largest file still under budget, and finishes with a free lossless `gifsicle -O3`.

**Fitting is not optional.** If nothing on the ladder fits, it keeps shrinking until something does. A GIF that Discord refuses is not a result.

On a 20-second 1280×720 screen recording this lands at full resolution, 12.5 fps, 9.4 MB, in about a minute and a half. Short clips take a few seconds.

### Why not just send an MP4?

You usually should — Discord autoplays them and they look far better at the same size. GIF's 256-colour palette costs more quality than the size limit does. MagCopy makes GIFs because sometimes you specifically need a `.gif`.

### Tuned for camera footage instead?

Flip the preference in `magcopy/optimize.py`: walk the ladder frame-rate-outermost rather than scale-outermost. For video, smoothness carries the clip and resolution is what you can afford to lose.

## What it borrows from OBS

- **A 1 ms system timer** (`timeBeginPeriod`) for the length of the capture, plus a short spin at the end of each wait. Windows' default scheduling granularity is 15.6 ms, which cannot pace a 25 fps loop at all — frames arrive in clumps. With it, a 4-second capture at 25 fps lands 101 frames and drifts 40 ms.
- **Above-normal thread priority** while recording, so an unrelated busy process can't shear the capture.
- **Timing anchored to the wall clock**, not to a frame counter. If a capture overruns its slot the loop repeats the previous frame rather than letting the recording slide quietly out of sync with what actually happened. Repeats are counted and reported.

**What it doesn't borrow:** OBS captures through Windows Graphics Capture / DXGI desktop duplication. That is genuinely better — hardware-accelerated, and it can see GPU-composited and fullscreen-exclusive windows that GDI returns as black. It also needs WinRT and D3D11 interop, which is well past what `ctypes` should be asked to do. GDI with `CAPTUREBLT` covers the desktop and application UI, which is what this tool is for. **Full-screen games are the known gap.**

## Settings

Everything lives in the window, and in `settings.json` next to the script (or `%APPDATA%\MagCopy\` when frozen).

| | |
|---|---|
| **theme** | dusk, night, ember, tide, paper |
| **window size** | small → x-large |
| **recording** | 50 / 25 / 20 fps, cursor on or off |
| **max length** | 10 / 20 / 30 / 60 seconds |
| **size limit** | 8 / 10 / 25 / 50 MB — Discord gives 10 free, more with Nitro |
| **after** | copy the GIF to the clipboard as a file, open the folder, also save screenshots |
| **startup** | start with Windows |

The finished GIF goes on the clipboard as a *file*, so Ctrl+V in Discord attaches it rather than pasting a path.

## Editing

The editor opens on its own after a recording. Scrub the filmstrip, drag the handles to trim, set a speed, save. Space plays, Ctrl+S saves, Escape discards.

Preview frames are extracted once as small JPEGs and paged in on demand — scrubbing a video file through a decoder is far too slow to feel like scrubbing. The trim is still expressed in seconds against the full-quality master, so nothing about the preview limits the output.

## Building a standalone .exe

```powershell
python -m pip install pyinstaller
.\build.ps1
```

The result is a single `dist\MagCopy.exe` with the fonts and encoders inside it (large, because ffmpeg is). Settings move to `%APPDATA%\MagCopy\`.

## Look and feel

Are.na's colour ladders, the layout language of the magnus archive site — hairlines, Geist and Geist Mono, lowercase mono labels, pill controls — in a frameless, rounded window. Same as [Opmize](https://github.com/magnusholmes-cmyk/Opmize), which is where the design came from. Rounded corners need Windows 11; on Windows 10 they stay square.

## Licences

MagCopy is MIT (see `LICENSE`). The tools it runs are separate programs, invoked as subprocesses, under their own licences:

| | |
|---|---|
| [ffmpeg](https://ffmpeg.org) | GPL (the gyan.dev "essentials" build) |
| [gifski](https://gif.ski) | AGPL-3.0 |
| [gifsicle](https://www.lcdf.org/gifsicle/) | GPL-2.0 |
| [Geist / Geist Mono](https://vercel.com/font) | SIL Open Font Licence — see `fonts/LICENSE-Geist-OFL.txt` |

They are not bundled in this repository; `tools/fetch_binaries.py` downloads them.

## Known limits

- Windows only. Capture, clipboard, hotkeys and the frameless window are all Win32.
- GDI capture cannot see fullscreen-exclusive games or some GPU-composited surfaces; those come out black.
- A shortcut already owned by another application will fail to register. MagCopy says so and keeps the previous one.
- Long recordings at high resolution take a while to encode. The status line tells you what it is trying.

## Tests

```bash
python tools/run_all_tests.py
```

Seven smoke tests, about five minutes. They drive the real region selector, measure recording
pace against the wall clock, walk every theme and window size, run a real recording through the
editor and out the other side as a GIF, and check the emitted GIF by parsing its block structure
rather than trusting the encoder.
