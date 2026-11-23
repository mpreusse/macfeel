"""macfeel-settings: the settings window (GTK4 + libadwaita).

Single instance: launching it again just raises the window.
All changes go to the daemon via the control socket (never to files).

    python3 -m macfeel.settings
"""

import math
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from . import config as cfg  # noqa: E402
from . import curve  # noqa: E402
from .gnome import GnomeMouse  # noqa: E402
from .uilink import DaemonLink  # noqa: E402

APP_ID = "io.github.macfeel.Settings"
PRESETS = list(curve.PRESETS)                      # keys, in display order
STEPS = curve.MAC_SLIDER_STEPS
DEFAULT_STEP = STEPS.index(curve.DEFAULT_TRACKING)
ALGORITHMS = {"table": "Table (current macOS)", "parametric": "Parametric (macOS 10.11–10.12)"}
SEND_DELAY_MS = 150                                # coalesce slider drags into one config write
TOGGLEABLE = ("disabled by user", "combined keyboard/mouse device")


def nearest_step(value):
    return min(range(len(STEPS)), key=lambda i: abs(STEPS[i] - value))


def step_label(index):
    diff = index - DEFAULT_STEP
    if diff == 0:
        return "Mac default"
    word = "slower" if diff < 0 else "faster"
    return f"{abs(diff)} step{'s' if abs(diff) > 1 else ''} {word}"


class CurveGraph(Gtk.DrawingArea):
    """Gain vs. mouse speed for the current settings, with a live dot.

    x: counts per report (how far the mouse moved in one report = speed).
    y: pixels per count, relative to Apple's default slow-speed gain
       (1× = precise slow movement). Dashed: the Mac default, for comparison.
    """

    X_MAX = 150.0  # counts per report at 400 dpi; the x axis is sqrt-scaled

    def __init__(self):
        super().__init__()
        self.set_content_height(230)
        self.set_hexpand(True)
        self.set_draw_func(self.draw)
        self.dpi = curve.MAC_DEFAULT_DPI
        self.pointer = {}
        self.reference = curve.MacCurve()
        self.unit = self.reference.base          # px per count that is shown as "1×"
        self.curve = self.reference
        self.dot = None                          # (counts, gain) of the last live report
        self.dot_alpha = 0.0

    def set_pointer(self, pointer, dpi=None):
        self.pointer = pointer
        self.dpi = dpi or self.dpi
        self.curve = curve.from_config(pointer, self.dpi)
        self.reference = curve.MacCurve(dpi=self.dpi)
        self.queue_draw()

    def set_live(self, live):
        if live.get("moving"):
            self.dot = (live["counts"], live["gain"] / self.unit)
            self.dot_alpha = 1.0
        else:
            self.dot_alpha *= 0.85
        self.queue_draw()

    def draw(self, area, cr, width, height):
        fg = self.get_color()
        accent = Adw.StyleManager.get_default().get_accent_color_rgba()
        left, right, top, bottom = 36, 12, 10, 26
        w, h = width - left - right, height - top - bottom

        samples = [self.X_MAX * (i / 200) ** 2 for i in range(1, 201)]
        gains = [self.curve.px_per_count(v) / self.unit for v in samples]
        ref = [self.reference.px_per_count(v) / self.unit for v in samples]
        y_max = max(4.0, math.ceil(max(gains + ref) + 0.5))

        def px(v, g):
            return left + w * math.sqrt(v / self.X_MAX), top + h * (1 - g / y_max)

        # grid + y labels
        cr.set_line_width(1)
        step = 1 if y_max <= 6 else 2
        for g in range(0, int(y_max) + 1, step):
            _, y = px(0, g)
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.12)
            cr.move_to(left, y)
            cr.line_to(left + w, y)
            cr.stroke()
            self.text(cr, f"{g}×", 4, y - 8, fg, 0.6)
        self.text(cr, "slow", left, top + h + 6, fg, 0.6)
        self.text(cr, "mouse speed", left + w / 2 - 38, top + h + 6, fg, 0.6)
        self.text(cr, "fast", left + w - 26, top + h + 6, fg, 0.6)

        def polyline(values):
            for i, (v, g) in enumerate(zip(samples, values)):
                (cr.line_to if i else cr.move_to)(*px(v, g))

        # Mac default (dashed reference), then the active curve
        cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.35)
        cr.set_line_width(1.5)
        cr.set_dash([5, 4])
        polyline(ref)
        cr.stroke()
        cr.set_dash([])
        cr.set_source_rgba(accent.red, accent.green, accent.blue, 1)
        cr.set_line_width(2.5)
        polyline(gains)
        cr.stroke()

        if self.dot and self.dot_alpha > 0.05:
            v, g = self.dot
            x, y = px(min(v, self.X_MAX), min(g, y_max))
            cr.set_source_rgba(accent.red, accent.green, accent.blue, 0.25 * self.dot_alpha)
            cr.arc(x, y, 11, 0, 2 * math.pi)
            cr.fill()
            cr.set_source_rgba(accent.red, accent.green, accent.blue, self.dot_alpha)
            cr.arc(x, y, 5, 0, 2 * math.pi)
            cr.fill()

    def text(self, cr, s, x, y, color, alpha):
        layout = self.create_pango_layout(s)
        cr.set_source_rgba(color.red, color.green, color.blue, alpha)
        cr.move_to(x, y)
        from gi.repository import PangoCairo
        PangoCairo.show_layout(cr, layout)


class Window(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="MacFeel", default_width=620, default_height=900)
        self.config = None
        self.status = None
        self.updating = False       # True while we set widgets from daemon data
        self.pending = {}
        self.pending_timer = None
        self.device_signature = None
        self.device_rows = []
        self.gnome = GnomeMouse()

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        self.banner = Adw.Banner(title="GNOME's own mouse acceleration is on — curves are stacking",
                                 button_label="Fix")
        self.banner.connect("button-clicked", lambda *_: self.gnome.make_flat())
        toolbar.add_top_bar(self.banner)

        self.stack = Gtk.Stack()
        offline = Adw.StatusPage(icon_name="input-mouse-symbolic", title="MacFeel is not running",
                                 description="Start the service with\n<tt>sudo systemctl start macfeeld</tt>")
        offline.set_description(offline.get_description())
        self.page = Adw.PreferencesPage()
        self.stack.add_named(offline, "offline")
        self.stack.add_named(self.page, "main")
        toolbar.set_content(self.stack)
        self.set_content(toolbar)

        self.build()
        self.gnome.settings.connect("changed::accel-profile", lambda *_: self.update_banner())
        self.link = DaemonLink(["status", "live"], self.on_message, self.on_connected)
        self.on_connected(False)
        self.link.start()

    # --- layout ------------------------------------------------------------

    def build(self):
        page = self.page

        general = Adw.PreferencesGroup()
        self.enabled_row = Adw.SwitchRow(title="Mac-like mouse",
                                         subtitle="Turn off for stock Ubuntu behaviour")
        self.enabled_row.connect("notify::active", self.on_enabled)
        general.add(self.enabled_row)
        page.add(general)

        pointer = Adw.PreferencesGroup(
            title="Pointer",
            description="The dot shows your mouse live. Dashed: the macOS default curve.")
        self.graph = CurveGraph()
        card = Gtk.Box(css_classes=["card"], margin_bottom=12)
        self.graph.set_margin_top(8)
        self.graph.set_margin_bottom(8)
        self.graph.set_margin_start(8)
        self.graph.set_margin_end(8)
        card.append(self.graph)
        pointer.add(card)

        row, self.tracking_scale, self.tracking_label = self.scale_row(
            "Tracking speed", 0, len(STEPS) - 1, 1,
            [(0, "Slow")] + [(i, None) for i in range(1, len(STEPS) - 1)] + [(len(STEPS) - 1, "Fast")])
        self.tracking_scale.set_round_digits(0)
        self.tracking_scale.connect("value-changed", self.on_tracking)
        pointer.add(row)

        row, self.speed_scale, self.speed_label = self.scale_row(
            "Overall speed", 50, 200, 5, [(100, None)])
        self.speed_scale.connect("value-changed", self.on_speed)
        pointer.add(row)

        self.preset_row = Adw.ComboRow(title="Curve", model=Gtk.StringList.new(list(curve.PRESETS.values())))
        self.preset_row.connect("notify::selected", self.on_preset)
        pointer.add(self.preset_row)

        advanced = Adw.ExpanderRow(title="Advanced")
        self.accel_row = self.spin_row("Acceleration strength", "1 = exactly Apple's curve, 0 = none",
                                       0, 1, 0.05, 2, ("pointer", "acceleration"))
        self.algo_row = Adw.ComboRow(
            title="Mac algorithm",
            subtitle="Current macOS uses the table; 10.11–10.12 used parametric curves",
            model=Gtk.StringList.new(list(ALGORITHMS.values())))
        self.algo_row.connect("notify::selected", lambda r, _: self.change(
            {"pointer": {"algorithm": list(ALGORITHMS)[r.get_selected()]}}))
        self.rate_row = self.spin_row(
            "Normalize report rate", "Hz. 0 = per report, exactly like macOS. Set to the mouse's report "
            "rate on your Mac if it polls at a different rate here", 0, 1000, 1, 0, ("pointer", "report_rate"))
        self.rate_info = Adw.ActionRow(title="This mouse reports at", subtitle="move the mouse…")
        for r in (self.accel_row, self.algo_row, self.rate_row, self.rate_info):
            advanced.add_row(r)
        pointer.add(advanced)
        page.add(pointer)

        scrolling = Adw.PreferencesGroup(title="Scrolling",
                                         description="Fast spins scroll further; slow notches stay 1:1.")
        self.scroll_row = Adw.SwitchRow(title="Scroll acceleration")
        self.scroll_row.connect("notify::active", lambda r, _: self.change({"scroll": {"enabled": r.get_active()}}))
        scrolling.add(self.scroll_row)
        row, self.vscroll_scale, self.vscroll_label = self.scale_row("Wheel", 0, 2, 0.1, [(0, "1:1"), (1, None)])
        self.vscroll_scale.connect("value-changed", self.on_scroll_scale, "vertical_strength", self.vscroll_label)
        scrolling.add(row)
        row, self.hscroll_scale, self.hscroll_label = self.scale_row("Thumb wheel", 0, 2, 0.1, [(0, "1:1"), (1, None)])
        self.hscroll_scale.connect("value-changed", self.on_scroll_scale, "horizontal_strength", self.hscroll_label)
        scrolling.add(row)
        page.add(scrolling)

        self.devices_group = Adw.PreferencesGroup(title="Mice")
        page.add(self.devices_group)

        test = Adw.PreferencesGroup(title="Try it", description="Move the pointer and scroll here.")
        lines = "\n".join(f"Line {i}: the quick brown fox jumps over the lazy dog" for i in range(1, 301))
        scroller = Gtk.ScrolledWindow(min_content_height=200, css_classes=["card"])
        scroller.set_child(Gtk.Label(label=lines, xalign=0, margin_start=12, margin_top=8, margin_bottom=8,
                                     selectable=True))
        test.add(scroller)
        page.add(test)

        notes = Adw.PreferencesGroup()
        notes.add(Gtk.Label(
            wrap=True, xalign=0, css_classes=["dim-label", "caption"],
            label="While MacFeel is on, GNOME's own mouse acceleration is switched off and its speed slider "
                  "set to the middle; both are restored when you turn MacFeel off. GNOME's speed slider "
                  "still works as an extra multiplier."))
        page.add(notes)

    def scale_row(self, title, lo, hi, step, marks):
        row = Adw.PreferencesRow(activatable=False)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                      margin_top=10, margin_bottom=4, margin_start=12, margin_end=12)
        head = Gtk.Box()
        head.append(Gtk.Label(label=title, xalign=0, hexpand=True))
        value = Gtk.Label(css_classes=["dim-label"])
        head.append(value)
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, lo, hi, step)
        scale.set_draw_value(False)
        for at, label in marks:
            scale.add_mark(at, Gtk.PositionType.BOTTOM, label)
        box.append(head)
        box.append(scale)
        row.set_child(box)
        return row, scale, value

    def spin_row(self, title, subtitle, lo, hi, step, digits, key):
        row = Adw.SpinRow.new_with_range(lo, hi, step)
        row.set_title(title)
        row.set_subtitle(subtitle)
        row.set_digits(digits)
        section, name = key
        row.connect("notify::value", lambda r, _: self.change({section: {name: r.get_value()}}))
        return row

    # --- daemon data -> widgets ----------------------------------------------

    def on_connected(self, connected):
        if connected:
            result = self.link.request("get_config")
            if result:
                self.load_config(result["config"])
        self.stack.set_visible_child_name("main" if connected else "offline")

    def on_message(self, msg):
        if msg.get("event") == "live":
            self.graph.set_live(msg)
            if msg.get("moving"):
                self.rate_info.set_subtitle(f"≈ {msg['rate']:.0f} reports per second")
        elif msg.get("event") == "status":
            self.status = msg
            self.updating = True
            self.enabled_row.set_active(msg["enabled"])
            self.updating = False
            self.update_devices(msg["devices"])
            dpis = [d["dpi"] for d in msg["devices"] if d["grabbed"] and d["dpi"]]
            if dpis and self.config:
                self.graph.set_pointer(self.config["pointer"], dpis[0])
            self.update_banner()

    def load_config(self, conf):
        self.config = conf
        p, s = conf["pointer"], conf["scroll"]
        self.updating = True
        self.enabled_row.set_active(conf["enabled"])
        self.tracking_scale.set_value(nearest_step(p["tracking_speed"]))
        self.update_tracking_label(p["tracking_speed"])
        self.speed_scale.set_value(round(p.get("speed", 1.0) * 100))
        self.speed_label.set_label(f"{round(p.get('speed', 1.0) * 100)} %")
        self.preset_row.set_selected(PRESETS.index(p["preset"]) if p["preset"] in PRESETS else 0)
        self.accel_row.set_value(p["acceleration"])
        self.algo_row.set_selected(list(ALGORITHMS).index(p.get("algorithm", "table")))
        self.rate_row.set_value(p.get("report_rate", 0))
        self.scroll_row.set_active(s["enabled"])
        for scale, label, key in ((self.vscroll_scale, self.vscroll_label, "vertical_strength"),
                                  (self.hscroll_scale, self.hscroll_label, "horizontal_strength")):
            scale.set_value(s[key])
            label.set_label(self.scroll_text(s[key]))
        self.updating = False
        self.graph.set_pointer(p)

    def update_tracking_label(self, value):
        self.tracking_label.set_label(f"{step_label(nearest_step(value))} ({value:g})")

    @staticmethod
    def scroll_text(v):
        return "off (1:1)" if v <= 0 else f"{v:.1f}×"

    def update_banner(self):
        st = self.status
        stacking = bool(st and st["enabled"] and st["grabbed"]) and not self.gnome.is_flat()
        self.banner.set_revealed(stacking)

    def update_devices(self, devices):
        signature = tuple((d["id"], d["name"], d["reason"], d["grabbed"], d["dpi"]) for d in devices)
        if signature == self.device_signature:
            return
        self.device_signature = signature
        for row in self.device_rows:
            self.devices_group.remove(row)
        self.device_rows = []
        conf_devices = (self.config or cfg.DEFAULTS)["devices"]
        for dev in devices:
            toggleable = dev["id"] and (dev["grabbed"] or dev["reason"] in TOGGLEABLE)
            if not toggleable:
                row = Adw.ActionRow(title=dev["name"], subtitle=dev["reason"], sensitive=False)
            else:
                row = Adw.ExpanderRow(title=dev["name"], subtitle=dev["reason"])
                use = Adw.SwitchRow(title="Use MacFeel for this mouse", active=dev["grabbed"])
                use.connect("notify::active", self.on_device_enabled, dev["id"])
                row.add_row(use)
                dpi = Adw.SpinRow.new_with_range(100, 8000, 50)
                dpi.set_title("Resolution macOS assumes")
                dpi.set_subtitle("dpi · lower is faster · macOS uses 400 for most mice, including the MX Master")
                dpi.set_value(conf_devices.get(dev["id"], {}).get("dpi") or dev["dpi"] or curve.MAC_DEFAULT_DPI)
                dpi.connect("notify::value", self.on_device_dpi, dev["id"])
                row.add_row(dpi)
            self.devices_group.add(row)
            self.device_rows.append(row)
        if not devices:
            row = Adw.ActionRow(title="No mouse found", subtitle="Connect a mouse; it appears here automatically")
            self.devices_group.add(row)
            self.device_rows.append(row)

    # --- widgets -> daemon ---------------------------------------------------

    def change(self, changes):
        """Queue a partial config change; sent after a short pause so drags write once."""
        if self.updating or self.config is None:
            return
        self.pending = cfg.merge(self.pending, changes)
        self.config = cfg.merge(self.config, changes)
        self.graph.set_pointer(self.config["pointer"])
        if self.pending_timer:
            GLib.source_remove(self.pending_timer)
        self.pending_timer = GLib.timeout_add(SEND_DELAY_MS, self.flush)

    def flush(self):
        self.pending_timer = None
        changes, self.pending = self.pending, {}
        if changes:
            self.link.request("set_config", changes=changes)
        return False

    def on_enabled(self, row, _):
        if not self.updating:
            self.link.request("set_enabled", enabled=row.get_active())

    def on_tracking(self, scale):
        value = STEPS[int(round(scale.get_value()))]
        self.update_tracking_label(value)
        self.change({"pointer": {"tracking_speed": value}})

    def on_speed(self, scale):
        pct = int(round(scale.get_value()))
        self.speed_label.set_label(f"{pct} %")
        self.change({"pointer": {"speed": pct / 100}})

    def on_preset(self, row, _):
        self.change({"pointer": {"preset": PRESETS[row.get_selected()]}})

    def on_scroll_scale(self, scale, key, label):
        value = round(scale.get_value(), 1)
        label.set_label(self.scroll_text(value))
        self.change({"scroll": {key: value}})

    def on_device_enabled(self, row, _, dev_id):
        settings = {"enabled": row.get_active()}
        if row.get_active():
            settings["allow_combined"] = True
        self.link.request("set_device", id=dev_id, settings=settings)

    def on_device_dpi(self, row, _, dev_id):
        self.change({"devices": {dev_id: {"dpi": int(row.get_value())}}})


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

    def do_activate(self):
        (self.props.active_window or Window(self)).present()


def main():
    return App().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
