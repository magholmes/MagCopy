"""The application: one window of settings, two shortcuts, and the flows behind them.

Threading
  Hotkeys arrive on the hotkey thread, the tray menu on the tray thread, and recording and
  encoding on workers of their own. None of them may touch Tk: Tkinter is not thread-safe, and
  even `after()` reaches into the interpreter's command table, which raises "main thread is not
  in main loop" if it loses the race. So every thread hands work over by putting a callable on
  `self._events`, and `_pump` - which runs on the Tk loop - is the only thing that calls it.

The screenshot path
  The region selector has already frozen the whole desktop to let you aim, so the screenshot is
  cropped out of that same frozen frame rather than grabbed again. It is faster, and it
  guarantees the picture is exactly what was framed - a second grab could catch a changed screen
  or the overlay itself on the way down.
"""
import os
import queue
import tempfile
import threading
import time
import tkinter as tk
from tkinter import filedialog

import numpy as np

from . import win, overlay
from .binaries import TOOLS
from .editor import GifEditor
from .optimize import gif_info
from .recorder import RecordFrame, Recorder
from .settings import (APP_NAME, APP_VERSION, DEFAULTS, RES_DIR, APP_DIR, SETTINGS_DIR,
                       get_autostart, is_first_run, load, log_exc, save, save_dir, set_autostart,
                       stamped_name)
from .theme import (Button, DotToggle, Fonts, Hairline, HotkeyField, IconButton, Label, Panel,
                    Pills, THEME_ORDER, Theme, TextLink, register_fonts)
from .tray import Tray

UI_SCALES = [("small", 0.85), ("medium", 1.0), ("large", 1.2), ("x-large", 1.45)]
FPS_CHOICES = [(50, "50"), (25, "25"), (20, "20")]
LENGTH_CHOICES = [(10, "10s"), (20, "20s"), (30, "30s"), (60, "60s")]
LIMIT_CHOICES = [(8.0, "8"), (10.0, "10"), (25.0, "25"), (50.0, "50")]


class App:
    def __init__(self, root):
        self.root = root
        first_run = is_first_run()
        self.settings = load()
        # "start with Windows" is a registry entry, so the registry is the truth after the first
        # run - otherwise turning it off would come back on at every launch. The default only gets
        # to act once, when there is no settings file yet.
        if (first_run and DEFAULTS["start_with_windows"] and not get_autostart()
                and not os.environ.get("MAGCOPY_NO_AUTOSTART")):
            set_autostart(True)                           # MAGCOPY_NO_AUTOSTART is for the tests
        self.settings["start_with_windows"] = get_autostart()
        if first_run:
            save(self.settings)
        self.dpi = max(0.75, root.winfo_fpixels("1i") / 96.0) if os.name == "nt" else 1.0
        self.ui_factor = float(self.settings.get("ui_scale", 1.0))
        self.theme = Theme(self.settings["theme"])
        self.scale = self.dpi * self.ui_factor
        self.fonts = Fonts(root, self.ui_factor)
        self.recent = []
        self.busy = False                     # a capture flow is in progress
        self.recorder = None
        self.record_frame = None
        self.editor = None
        self.hwnd = None
        self._toast = None
        self._toast_job = None
        self._events = queue.Queue()
        self._alive = True

        root.title(APP_NAME)
        root.configure(bg=self.theme.c["bg"])
        root.resizable(False, False)
        root.report_callback_exception = lambda t, v, tb: log_exc("tk callback")
        self._build()
        self._fit()
        root.bind("<Map>", lambda e: self._frameless() if e.widget is root else None)
        root.after(0, self._frameless)
        root.after(30, self._pump)
        root.protocol("WM_DELETE_WINDOW", self.hide_window)
        overlay.prewarm(root)

        self.hotkeys = win.HotkeyManager()
        self._bind_hotkeys()
        self.tray = Tray(self._icon_path(), "%s %s" % (APP_NAME, APP_VERSION),
                         [("Open MagCopy", "open"), (None, None),
                          ("Screenshot to clipboard", "shot"), ("Record a GIF", "gif"),
                          (None, None), ("Open captures folder", "folder"),
                          (None, None), ("Quit", "quit")],
                         on_command=lambda k: self.post(lambda: self._tray_command(k)),
                         on_activate=lambda: self.post(self.show_window))
        self.tray.start()

    # ----------------------------------------------------------------- thread hand-off
    def post(self, fn):
        """Called from any thread: queue `fn` to run on the Tk loop."""
        try:
            self._events.put(fn)
        except Exception:
            pass

    def _pump(self):
        while True:
            try:
                fn = self._events.get_nowait()
            except queue.Empty:
                break
            try:
                fn()
            except Exception:
                log_exc("queued callback")
        if self._alive:
            self.root.after(30, self._pump)

    # ----------------------------------------------------------------- hotkeys
    def _bind_hotkeys(self):
        bindings = [("screenshot", self.settings["hotkey_shot"],
                     lambda: self.post(self.start_screenshot)),
                    ("gif", self.settings["hotkey_gif"], lambda: self.post(self.start_gif))]
        failed = (self.hotkeys.rebind(bindings) if self.hotkeys._thread
                  else self.hotkeys.start(bindings))
        if failed:
            names = ", ".join(failed)
            self.root.after(300, lambda: self.toast(
                "another app already owns: %s" % names, error=True))
            # a shortcut that silently does not work is the worst failure this app has: say so
            # where it can be seen even when no window is open
            self.root.after(900, lambda: self._warn_hotkey(names))
        return failed

    def _warn_hotkey(self, names):
        try:
            self.tray.notify(APP_NAME, "Could not claim: %s. Another app (or an older copy of "
                                       "MagCopy) already owns it - open MagCopy and pick a "
                                       "different shortcut." % names)
        except Exception:
            pass

    def _set_hotkey(self, which, combo):
        key = "hotkey_shot" if which == "shot" else "hotkey_gif"
        other = "hotkey_gif" if which == "shot" else "hotkey_shot"
        if combo == self.settings[other]:
            self.toast("that shortcut is already used by the other action", error=True)
            (self.field_shot if which == "shot" else self.field_gif).set(self.settings[key])
            return
        previous = self.settings[key]
        self.settings[key] = combo
        failed = self._bind_hotkeys()
        if failed:
            self.settings[key] = previous                 # roll back to something that works
            self._bind_hotkeys()
            (self.field_shot if which == "shot" else self.field_gif).set(previous)
        else:
            save(self.settings)
            self.toast("shortcut set to %s" % combo)

    # ----------------------------------------------------------------- flows
    def _hide_for_capture(self):
        visible = bool(self.root.winfo_viewable())
        if visible:
            self.root.withdraw()
            self.root.update()
            time.sleep(0.12)                              # let the compositor finish the fade
        return visible

    def start_screenshot(self):
        if self.busy:
            return
        self.busy = True
        was_visible = self._hide_for_capture()
        try:
            sel = overlay.RegionSelector(self.root, self.theme, self.fonts,
                                         "drag to copy a screenshot   ·   shift = square   ·   esc cancels")
            rect = sel.run()
            if not rect:
                return
            x, y, wd, ht = rect
            crop = sel.bright.crop((x - sel.vx, y - sel.vy, x - sel.vx + wd, y - sel.vy + ht))
            rgb = np.asarray(crop, dtype=np.uint8)
            bgra = np.dstack([rgb[:, :, ::-1], np.full(rgb.shape[:2], 255, np.uint8)])
            ok = win.set_clipboard_image(bgra)
            note = "%d × %d copied" % (wd, ht)
            if self.settings["save_screenshots"]:
                path = os.path.join(save_dir(self.settings), stamped_name("shot", "png"))
                crop.save(path, "PNG")
                self._remember(path, "%d × %d" % (wd, ht), os.path.getsize(path))
                note += " · saved"
            self.toast(note if ok else "could not reach the clipboard", error=not ok)
        except Exception:
            log_exc("screenshot")
            self.toast("the screenshot failed", error=True)
        finally:
            self.busy = False
            if was_visible:
                self.root.deiconify()

    def start_gif(self):
        if self.recorder:                                 # the same shortcut stops a recording
            self.stop_recording()
            return
        if self.busy:
            return
        self.busy = True
        was_visible = self._hide_for_capture()
        try:
            # live=True: a recording is about to capture motion, so the screen must keep moving
            # while the region is chosen. Screenshots still freeze, which is right for a still.
            sel = overlay.RegionSelector(
                self.root, self.theme, self.fonts,
                "drag to record   ·   %s or esc stops   ·   up to %ds"
                % (self.settings["hotkey_gif"], self.settings["max_seconds"]), live=True)
            rect = sel.run()
            if not rect:
                self.busy = False
                if was_visible:
                    self.root.deiconify()
                return
            self._begin_recording(rect)
        except Exception:
            log_exc("gif start")
            self.busy = False
            self.toast("could not start recording", error=True)

    def _begin_recording(self, rect):
        master = os.path.join(tempfile.mkdtemp(prefix="magcopy-rec-"), "master.mp4")
        self.record_frame = RecordFrame(self.root, self.theme, self.fonts, rect,
                                        self.stop_recording, self.cancel_recording, self.scale)
        if self.settings["show_recording_frame"]:
            self.record_frame.show()
        self.recorder = Recorder(
            rect, master, fps=self.settings["record_fps"],
            max_seconds=self.settings["max_seconds"], cursor=self.settings["capture_cursor"],
            on_tick=lambda t: self.post(lambda: self._record_tick(t)),
            on_done=lambda rec: self.post(lambda: self._record_done(rec)))
        self.recorder.start()

    def _record_tick(self, seconds):
        if self.record_frame:
            self.record_frame.update_time(seconds, self.settings["max_seconds"])

    def stop_recording(self):
        if self.recorder:
            self.recorder.stop()

    def cancel_recording(self):
        if self.recorder:
            self.recorder.cancel()

    def _record_done(self, rec):
        if self.record_frame:
            self.record_frame.destroy()
            self.record_frame = None
        self.recorder = None
        self.busy = False
        if rec.cancelled:
            return
        if rec.error:
            self.toast(rec.error, error=True)
            return
        if rec.repeated:
            self.toast("recorded %.1fs (%d frames repeated to keep timing)"
                       % (rec.duration, rec.repeated))
        self.editor = GifEditor(self, rec.out_path)
        self.editor.open()

    def editor_closed(self, editor):
        if self.editor is editor:
            self.editor = None

    def gif_saved(self, res):
        note = "%d × %d · %.2f MB" % (res.width, res.height, res.bytes / 1e6)
        if self.settings["copy_gif_to_clipboard"]:
            if win.set_clipboard_files([res.path]):
                note += " · copied, paste with ctrl+v"
        self._remember(res.path, "%d × %d · %.4g fps" % (res.width, res.height, res.fps), res.bytes)
        if self.settings["open_folder_after_gif"]:
            self._open(os.path.dirname(res.path))
        if not res.fits:
            self.toast("saved, but it is over the %.0f mb limit" % self.settings["size_limit_mb"],
                       error=True)
        else:
            self.toast("gif saved · " + note)
            self.tray.notify(APP_NAME, "GIF saved · " + note)

    # ----------------------------------------------------------------- window
    def _icon_path(self):
        for base in (RES_DIR, APP_DIR):
            p = os.path.join(base, "icon.ico")
            if os.path.exists(p):
                return p
        return None

    def _frameless(self):
        c = self.theme.c
        hwnd = win.make_frameless(self.root, c["hair"], c["bg"])
        if hwnd:
            self.hwnd = hwnd

    def _fit(self):
        self.root.update_idletasks()
        wd = int(round(620 * self.scale))
        ht = self.bgroot.winfo_reqheight()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        wd, ht = min(wd, sw - 40), min(ht, sh - 80)
        self.root.minsize(wd, ht)
        self.root.maxsize(wd, ht)
        self.root.geometry("%dx%d" % (wd, ht))

    def show_window(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def hide_window(self):
        """Closing the window leaves the shortcuts running; Quit is on the tray menu."""
        self.root.withdraw()
        if not getattr(self, "_told_about_tray", False):
            self._told_about_tray = True
            self.tray.notify(APP_NAME, "Still running - the shortcuts work. Right-click the tray "
                                       "icon to quit.")

    def minimize(self):
        self.root.withdraw()

    def quit(self):
        self._alive = False
        try:
            self.hotkeys.stop()
        except Exception:
            pass
        try:
            self.tray.stop()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

    def _tray_command(self, key):
        if key == "open":
            self.show_window()
        elif key == "shot":
            self.start_screenshot()
        elif key == "gif":
            self.start_gif()
        elif key == "folder":
            self._open(save_dir(self.settings))
        elif key == "quit":
            self.quit()

    def _open(self, path):
        try:
            os.startfile(path)
        except Exception:
            log_exc("open %s" % path)

    # ----------------------------------------------------------------- toast
    def toast(self, text, error=False):
        try:
            self.status.configure(text=text)
            self.status.set_role("error" if error else "mute")
            self.status.restyle(self.theme.c)
            if self._toast_job:
                self.root.after_cancel(self._toast_job)
            self._toast_job = self.root.after(6000, lambda: self._clear_toast())
        except Exception:
            pass
        if not self.root.winfo_viewable() and error:
            try:
                self.tray.notify(APP_NAME, text)
            except Exception:
                pass

    def _clear_toast(self):
        try:
            self.status.configure(text=self._idle_status())
            self.status.set_role("mute")
            self.status.restyle(self.theme.c)
        except Exception:
            pass

    def _idle_status(self):
        return "%s screenshot   ·   %s gif" % (self.settings["hotkey_shot"],
                                               self.settings["hotkey_gif"])

    # ----------------------------------------------------------------- recents
    def _remember(self, path, dims, size):
        self.recent.insert(0, dict(path=path, dims=dims, bytes=size))
        del self.recent[6:]
        self._render_recent()

    def _render_recent(self):
        for child in self.recent_box.winfo_children():
            child.destroy()
        S = lambda px: int(round(px * self.scale))
        T, F = self.theme, self.fonts
        if not self.recent:
            lab = Label(self.recent_box, role="mute2", text="nothing captured yet",
                        font=F.mono8, anchor="w")
            lab.pack(fill="x", pady=(S(8), S(8)))
            T.add(lab)
            return
        for item in self.recent:
            row = Panel(self.recent_box)
            row.pack(fill="x")
            T.add(row)
            name = TextLink(row, os.path.basename(item["path"]),
                            lambda p=item["path"]: self._open(p), F, role="ink2")
            name.pack(side="left", pady=(S(6), S(6)))
            T.add(name)
            meta = Label(row, role="mute2",
                         text="%s · %.2f mb" % (item["dims"], item["bytes"] / 1e6),
                         font=F.mono8, anchor="e")
            meta.pack(side="right")
            T.add(meta)

    # ----------------------------------------------------------------- settings handlers
    def _persist(self):
        save(self.settings)

    def set_theme(self, name):
        self.settings["theme"] = name
        self.theme.set(name)
        self.root.configure(bg=self.theme.c["bg"])
        self._frameless()
        self._render_recent()
        self._persist()

    def set_ui_scale(self, factor):
        if factor == self.ui_factor:
            return
        self.ui_factor = factor
        self.settings["ui_scale"] = factor
        self._persist()
        self.bgroot.destroy()
        self.theme.reset()
        self.scale = self.dpi * self.ui_factor
        self.fonts = Fonts(self.root, self.ui_factor)
        self._build()
        self._render_recent()
        self._fit()

    def _set(self, key, value, rebuild=False):
        self.settings[key] = value
        self._persist()

    def _choose_folder(self):
        d = filedialog.askdirectory(title="Where should captures be saved?",
                                    initialdir=save_dir(self.settings))
        if d:
            self.settings["save_dir"] = d
            self._persist()
            self.folder_label.configure(text=self._folder_text())

    def _folder_text(self):
        d = save_dir(self.settings)
        return d if len(d) < 52 else "…" + d[-50:]

    def _set_autostart(self, on):
        if not set_autostart(on):
            self.toast("could not change the startup setting", error=True)
            self.toggle_autostart.set(get_autostart())
            return
        self.settings["start_with_windows"] = on
        self._persist()

    # ----------------------------------------------------------------- layout
    def _build(self):
        S = lambda px: int(round(px * self.scale))
        T, F = self.theme, self.fonts
        PADX = S(24)
        self.bgroot = Panel(self.root)
        self.bgroot.pack(fill="both", expand=True)
        T.add(self.bgroot)

        # --- title band (drag handle)
        band = Panel(self.bgroot)
        band.pack(fill="x")
        T.add(band)
        chrome = Panel(band)
        chrome.pack(fill="x", padx=PADX, pady=(S(14), S(10)))
        T.add(chrome)
        word = Label(chrome, role="ink", text=APP_NAME.lower(), font=F.body_med, anchor="w")
        word.pack(side="left")
        T.add(word)
        ver = Label(chrome, role="mute2", text=APP_VERSION, font=F.mono8, anchor="w")
        ver.pack(side="left", padx=(S(8), 0))
        T.add(ver)
        btn_close = IconButton(chrome, "close", self.hide_window, self.scale)
        btn_min = IconButton(chrome, "minimize", self.minimize, self.scale)
        btn_close.pack(side="right", padx=(S(2), 0))
        btn_min.pack(side="right")
        T.add(btn_close)
        T.add(btn_min)
        hl = Hairline(band)
        hl.pack(fill="x", padx=PADX)
        T.add(hl)
        for widget in (band, chrome, word, ver, hl):
            widget.configure(cursor="fleur")
            widget.bind("<ButtonPress-1>", self._drag_start)
            widget.bind("<B1-Motion>", self._drag_move)

        # --- the two actions
        acts = Panel(self.bgroot)
        acts.pack(fill="x", padx=PADX, pady=(S(18), S(4)))
        T.add(acts)

        def action(parent, title, blurb, field_value, on_set, run):
            row = Panel(parent)
            row.pack(fill="x", pady=(0, S(14)))
            T.add(row)
            left = Panel(row)
            left.pack(side="left", fill="x", expand=True)
            T.add(left)
            head = Label(left, role="ink", text=title, font=F.body_med, anchor="w")
            head.pack(fill="x")
            T.add(head)
            sub = Label(left, role="mute", text=blurb, font=F.mono8, anchor="w")
            sub.pack(fill="x", pady=(S(2), 0))
            T.add(sub)
            go = TextLink(left, "run now", run, F)
            go.pack(anchor="w", pady=(S(4), 0))
            T.add(go)
            field = HotkeyField(row, F, field_value, on_set, self.scale)
            field.pack(side="right", padx=(S(12), 0))
            T.add(field)
            return field

        self.field_shot = action(
            acts, "screenshot", "drag a region · straight to the clipboard, no preview",
            self.settings["hotkey_shot"], lambda v: self._set_hotkey("shot", v),
            self.start_screenshot)
        self.field_gif = action(
            acts, "record a gif", "drag a region · trim it · sized to fit discord",
            self.settings["hotkey_gif"], lambda v: self._set_hotkey("gif", v), self.start_gif)

        hl1 = Hairline(self.bgroot)
        hl1.pack(fill="x", padx=PADX, pady=(S(2), S(14)))
        T.add(hl1)

        # --- settings rows
        opts = Panel(self.bgroot)
        opts.pack(fill="x", padx=PADX)
        T.add(opts)

        def row(label, gap=8):
            r = Panel(opts)
            r.pack(fill="x", pady=(0, S(gap)))
            T.add(r)
            lab = Label(r, role="mute", text=label, font=F.mono9, anchor="w", width=12)
            lab.pack(side="left")
            T.add(lab)
            return r

        r = row("theme")
        T.add(Pills(r, F, THEME_ORDER, self.settings["theme"], self.set_theme, self.scale)).pack(side="left")
        r = row("window size")
        T.add(Pills(r, F, [(v, n) for n, v in UI_SCALES], self.ui_factor, self.set_ui_scale,
                    self.scale)).pack(side="left")

        r = row("recording")
        T.add(Pills(r, F, FPS_CHOICES, self.settings["record_fps"],
                    lambda v: self._set("record_fps", v), self.scale)).pack(side="left")
        lab = Label(r, role="mute2", text="fps", font=F.mono8, anchor="w")
        lab.pack(side="left", padx=(S(6), S(12)))
        T.add(lab)
        self.toggle_cursor = DotToggle(r, F, "cursor", self.settings["capture_cursor"],
                                       lambda v: self._set("capture_cursor", v), self.scale)
        self.toggle_cursor.pack(side="left")
        T.add(self.toggle_cursor)
        self.toggle_frame = DotToggle(r, F, "show frame", self.settings["show_recording_frame"],
                                      lambda v: self._set("show_recording_frame", v), self.scale)
        self.toggle_frame.pack(side="left", padx=(S(6), 0))
        T.add(self.toggle_frame)

        r = row("max length")
        T.add(Pills(r, F, LENGTH_CHOICES, self.settings["max_seconds"],
                    lambda v: self._set("max_seconds", v), self.scale)).pack(side="left")

        r = row("size limit")
        T.add(Pills(r, F, LIMIT_CHOICES, self.settings["size_limit_mb"],
                    lambda v: self._set("size_limit_mb", v), self.scale)).pack(side="left")
        lab = Label(r, role="mute2", text="mb · discord gives 10 free, more with nitro",
                    font=F.mono8, anchor="w")
        lab.pack(side="left", padx=(S(6), 0))
        T.add(lab)

        r = row("save to")
        self.folder_label = Label(r, role="ink2", text=self._folder_text(), font=F.mono8, anchor="w")
        self.folder_label.pack(side="left")
        T.add(self.folder_label)
        change = TextLink(r, "change", self._choose_folder, F)
        change.pack(side="right")
        T.add(change)

        r = row("after")
        for text, key in (("copy gif to clipboard", "copy_gif_to_clipboard"),
                          ("open folder", "open_folder_after_gif"),
                          ("also save screenshots", "save_screenshots")):
            tog = DotToggle(r, F, text, self.settings[key],
                            lambda v, k=key: self._set(k, v), self.scale)
            tog.pack(side="left", padx=(0, S(6)))
            T.add(tog)

        r = row("startup", gap=0)
        self.toggle_autostart = DotToggle(r, F, "start with windows",
                                          self.settings["start_with_windows"],
                                          self._set_autostart, self.scale)
        self.toggle_autostart.pack(side="left")
        T.add(self.toggle_autostart)

        # --- recents
        sec = Panel(self.bgroot)
        sec.pack(fill="x", padx=PADX, pady=(S(18), 0))
        T.add(sec)
        st = Label(sec, role="ink", text="recent", font=F.body_med, anchor="w")
        st.pack(side="left")
        T.add(st)
        hl2 = Hairline(self.bgroot)
        hl2.pack(fill="x", padx=PADX, pady=(S(8), 0))
        T.add(hl2)
        self.recent_box = Panel(self.bgroot)
        self.recent_box.pack(fill="x", padx=PADX)
        T.add(self.recent_box)
        self._render_recent()

        # --- status + footer
        self.status = Label(self.bgroot, role="mute", text=self._idle_status(),
                            font=F.mono9, anchor="w")
        self.status.pack(fill="x", padx=PADX, pady=(S(14), 0))
        T.add(self.status)
        foot = Panel(self.bgroot)
        foot.pack(fill="x", padx=PADX, pady=(S(10), S(18)))
        T.add(foot)
        for text, cmd in (("open captures folder", lambda: self._open(save_dir(self.settings))),
                          ("settings file", lambda: self._open(SETTINGS_DIR)),
                          ("quit", self.quit)):
            lk = TextLink(foot, text, cmd, F)
            lk.pack(side="left", padx=(0, S(16)))
            T.add(lk)

    # ---- dragging the frameless window
    def _drag_start(self, e):
        if os.name == "nt" and self.hwnd:
            import ctypes
            import ctypes.wintypes as wt
            r = wt.RECT()
            ctypes.windll.user32.GetWindowRect(self.hwnd, ctypes.byref(r))
            self._d = (e.x_root - r.left, e.y_root - r.top)
        else:
            self._d = (e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y())

    def _drag_move(self, e):
        d = getattr(self, "_d", None)
        if not d:
            return
        x, y = e.x_root - d[0], e.y_root - d[1]
        if os.name == "nt" and self.hwnd:
            win.move_window(self.hwnd, x, y)
        else:
            self.root.geometry("+%d+%d" % (x, y))
