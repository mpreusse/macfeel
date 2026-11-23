"""A tray icon with a menu, spoken directly over D-Bus (no GUI toolkit).

Implements the two protocols that GNOME's AppIndicator extension (and KDE,
and other panels) understand:

  * org.kde.StatusNotifierItem - the icon, title, tooltip
  * com.canonical.dbusmenu     - the menu (labels, checkmarks, submenus, clicks)

Interfaces follow the extension's own definitions in
/usr/share/gnome-shell/extensions/ubuntu-appindicators@ubuntu.com/interfaces-xml/.

Usage:
    item = StatusNotifierItem("macfeel", icon_theme_path)
    item.set_icon("macfeel-active-symbolic", "Active")
    item.set_menu([MenuItem("Pause", on_pause), MenuItem.separator(), ...])
"""

import os

from gi.repository import Gio, GLib

ITEM_PATH = "/StatusNotifierItem"
MENU_PATH = "/MenuBar"
WATCHER = "org.kde.StatusNotifierWatcher"

SNI_XML = """
<node><interface name="org.kde.StatusNotifierItem">
  <property name="Category" type="s" access="read"/>
  <property name="Id" type="s" access="read"/>
  <property name="Title" type="s" access="read"/>
  <property name="Status" type="s" access="read"/>
  <property name="IconName" type="s" access="read"/>
  <property name="IconThemePath" type="s" access="read"/>
  <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
  <property name="Menu" type="o" access="read"/>
  <property name="ItemIsMenu" type="b" access="read"/>
  <method name="Activate"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
  <method name="SecondaryActivate"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
  <method name="ContextMenu"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
  <method name="Scroll"><arg name="delta" type="i" direction="in"/><arg name="orientation" type="s" direction="in"/></method>
  <signal name="NewIcon"/>
  <signal name="NewTitle"/>
  <signal name="NewToolTip"/>
  <signal name="NewStatus"><arg name="status" type="s"/></signal>
</interface></node>"""

MENU_XML = """
<node><interface name="com.canonical.dbusmenu">
  <property name="Version" type="u" access="read"/>
  <property name="TextDirection" type="s" access="read"/>
  <property name="Status" type="s" access="read"/>
  <property name="IconThemePath" type="as" access="read"/>
  <method name="GetLayout">
    <arg type="i" name="parentId" direction="in"/><arg type="i" name="recursionDepth" direction="in"/>
    <arg type="as" name="propertyNames" direction="in"/>
    <arg type="u" name="revision" direction="out"/><arg type="(ia{sv}av)" name="layout" direction="out"/>
  </method>
  <method name="GetGroupProperties">
    <arg type="ai" name="ids" direction="in"/><arg type="as" name="propertyNames" direction="in"/>
    <arg type="a(ia{sv})" name="properties" direction="out"/>
  </method>
  <method name="GetProperty">
    <arg type="i" name="id" direction="in"/><arg type="s" name="name" direction="in"/>
    <arg type="v" name="value" direction="out"/>
  </method>
  <method name="Event">
    <arg type="i" name="id" direction="in"/><arg type="s" name="eventId" direction="in"/>
    <arg type="v" name="data" direction="in"/><arg type="u" name="timestamp" direction="in"/>
  </method>
  <method name="EventGroup">
    <arg type="a(isvu)" name="events" direction="in"/><arg type="ai" name="idErrors" direction="out"/>
  </method>
  <method name="AboutToShow"><arg type="i" name="id" direction="in"/><arg type="b" name="needUpdate" direction="out"/></method>
  <method name="AboutToShowGroup">
    <arg type="ai" name="ids" direction="in"/><arg type="ai" name="updatesNeeded" direction="out"/>
    <arg type="ai" name="idErrors" direction="out"/>
  </method>
  <signal name="ItemsPropertiesUpdated"><arg type="a(ia{sv})"/><arg type="a(ias)"/></signal>
  <signal name="LayoutUpdated"><arg type="u" name="revision"/><arg type="i" name="parent"/></signal>
</interface></node>"""


class MenuItem:
    """One menu entry. `checked` None = no checkbox; True/False = checkbox state."""

    def __init__(self, label, callback=None, enabled=True, checked=None, children=None):
        self.label, self.callback, self.enabled = label, callback, enabled
        self.checked, self.children = checked, children
        self.is_separator = False
        self.id = 0

    @classmethod
    def separator(cls):
        item = cls("")
        item.is_separator = True
        return item

    def props(self):
        if self.is_separator:
            return {"type": GLib.Variant("s", "separator")}
        p = {"label": GLib.Variant("s", self.label),
             "enabled": GLib.Variant("b", bool(self.enabled and (self.callback or self.children)))}
        if self.checked is not None:
            p["toggle-type"] = GLib.Variant("s", "checkmark")
            p["toggle-state"] = GLib.Variant("i", 1 if self.checked else 0)
        if self.children:
            p["children-display"] = GLib.Variant("s", "submenu")
        return p


class StatusNotifierItem:
    def __init__(self, item_id, icon_theme_path="", title=""):
        self.item_id, self.icon_theme_path, self.title = item_id, icon_theme_path, title or item_id
        self.icon_name, self.tooltip = "", ""
        self.root = MenuItem("", children=[])
        self.items = {0: self.root}          # id -> MenuItem
        self.revision = 0
        self.connection = None
        self.bus_name = f"org.kde.StatusNotifierItem-{os.getpid()}-1"
        self.sni_info = Gio.DBusNodeInfo.new_for_xml(SNI_XML).interfaces[0]
        self.menu_info = Gio.DBusNodeInfo.new_for_xml(MENU_XML).interfaces[0]
        Gio.bus_own_name(Gio.BusType.SESSION, self.bus_name, Gio.BusNameOwnerFlags.NONE,
                         self._on_bus, None, None)

    # --- public API --------------------------------------------------------

    def set_icon(self, name, tooltip=""):
        changed = name != self.icon_name
        self.icon_name, self.tooltip = name, tooltip
        if changed:
            self._signal(ITEM_PATH, "org.kde.StatusNotifierItem", "NewIcon")
        self._signal(ITEM_PATH, "org.kde.StatusNotifierItem", "NewToolTip")

    def set_menu(self, items):
        """Replace the whole menu (cheap: the panel re-reads it on LayoutUpdated)."""
        self.root.children = items
        self.items = {0: self.root}

        def number(entries):
            for entry in entries:
                entry.id = len(self.items)
                self.items[entry.id] = entry
                number(entry.children or [])
        number(items)
        self.revision += 1
        self._signal(MENU_PATH, "com.canonical.dbusmenu", "LayoutUpdated",
                     GLib.Variant("(ui)", (self.revision, 0)))

    # --- D-Bus plumbing ----------------------------------------------------

    def _on_bus(self, connection, _name):
        self.connection = connection
        connection.register_object(ITEM_PATH, self.sni_info, self._item_call, self._item_prop, None)
        connection.register_object(MENU_PATH, self.menu_info, self._menu_call, self._menu_prop, None)
        # (Re-)register whenever a panel's watcher appears, e.g. after gnome-shell restarts.
        Gio.bus_watch_name_on_connection(connection, WATCHER, Gio.BusNameWatcherFlags.NONE,
                                         self._register, None)

    def _register(self, connection, _name, _owner):
        connection.call(WATCHER, "/StatusNotifierWatcher", WATCHER, "RegisterStatusNotifierItem",
                        GLib.Variant("(s)", (self.bus_name,)), None, Gio.DBusCallFlags.NONE,
                        -1, None, None, None)

    def _signal(self, path, interface, name, params=None):
        if self.connection:
            self.connection.emit_signal(None, path, interface, name, params)

    def _item_prop(self, _conn, _sender, _path, _iface, name):
        values = {
            "Category": GLib.Variant("s", "Hardware"),
            "Id": GLib.Variant("s", self.item_id),
            "Title": GLib.Variant("s", self.title),
            "Status": GLib.Variant("s", "Active"),
            "IconName": GLib.Variant("s", self.icon_name),
            "IconThemePath": GLib.Variant("s", self.icon_theme_path),
            "ToolTip": GLib.Variant("(sa(iiay)ss)", (self.icon_name, [], self.title, self.tooltip)),
            "Menu": GLib.Variant("o", MENU_PATH),
            "ItemIsMenu": GLib.Variant("b", True),
        }
        return values.get(name)

    def _item_call(self, _conn, _sender, _path, _iface, method, _params, invocation):
        invocation.return_value(None)   # clicks open the menu (ItemIsMenu); nothing else to do

    def _menu_prop(self, _conn, _sender, _path, _iface, name):
        values = {
            "Version": GLib.Variant("u", 3),
            "TextDirection": GLib.Variant("s", "ltr"),
            "Status": GLib.Variant("s", "normal"),
            "IconThemePath": GLib.Variant("as", [self.icon_theme_path]),
        }
        return values.get(name)

    def _layout(self, item, depth):
        children = []
        if depth != 0:
            children = [GLib.Variant("(ia{sv}av)", self._layout(child, depth - 1))
                        for child in item.children or []]
        props = {"children-display": GLib.Variant("s", "submenu")} if item is self.root else item.props()
        return item.id, props, children

    def _menu_call(self, _conn, _sender, _path, _iface, method, params, invocation):
        if method == "GetLayout":
            parent, depth, _names = params.unpack()
            item = self.items.get(parent, self.root)
            invocation.return_value(GLib.Variant("(u(ia{sv}av))", (self.revision, self._layout(item, depth))))
        elif method == "GetGroupProperties":
            ids, _names = params.unpack()
            ids = ids or list(self.items)
            result = [(i, self.items[i].props() if i else {}) for i in ids if i in self.items]
            invocation.return_value(GLib.Variant("(a(ia{sv}))", (result,)))
        elif method == "GetProperty":
            item_id, name = params.unpack()
            value = self.items[item_id].props().get(name) if item_id in self.items else None
            invocation.return_value(GLib.Variant("(v)", (value or GLib.Variant("s", ""),)))
        elif method == "Event":
            item_id, event, _data, _ts = params.unpack()
            self._event(item_id, event)
            invocation.return_value(None)
        elif method == "EventGroup":
            (events,) = params.unpack()
            for item_id, event, _data, _ts in events:
                self._event(item_id, event)
            invocation.return_value(GLib.Variant("(ai)", ([],)))
        elif method == "AboutToShow":
            invocation.return_value(GLib.Variant("(b)", (False,)))
        elif method == "AboutToShowGroup":
            invocation.return_value(GLib.Variant("(aiai)", ([], [])))
        else:
            invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method)

    def _event(self, item_id, event):
        item = self.items.get(item_id)
        if event == "clicked" and item and item.callback and item.enabled:
            # Run after the D-Bus reply, so a slow callback never blocks the panel.
            GLib.idle_add(lambda: item.callback() and False)
