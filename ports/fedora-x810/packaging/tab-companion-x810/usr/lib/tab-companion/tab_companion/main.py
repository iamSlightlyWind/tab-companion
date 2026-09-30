# SPDX-License-Identifier: MIT

def main(argv):
    if "--tui" in argv:
        from .tui import main as tui_main

        tui_argv = [arg for arg in argv if arg != "--tui"]
        return tui_main(tui_argv[1:])

    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gio, GLib

    from . import APP_ID
    from .admin_auth import available as admin_available, authorize_at_startup
    from .window import CompanionWindow
    import threading

    def authorize_then_load_boot_status(window):
        """Do not race startup authorization against the initial privileged read."""
        authorized = authorize_at_startup()
        if authorized:
            GLib.idle_add(window._boot_refresh)
        else:
            GLib.idle_add(window._boot_status_ready, {
                "current": None,
                "sets": [],
                "error": "Administrator authorization was cancelled.",
            })

    class CompanionApplication(Adw.Application):
        def __init__(self):
            super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
            self.connect("activate", self._activate)
            self._admin_auth_started = False

        def _activate(self, _app):
            window = self.props.active_window
            if window is None:
                window = CompanionWindow(self)
            window.present()
            if admin_available() and not self._admin_auth_started:
                self._admin_auth_started = True
                threading.Thread(
                    target=authorize_then_load_boot_status,
                    args=(window,),
                    daemon=True,
                ).start()
            elif not admin_available() and not self._admin_auth_started:
                self._admin_auth_started = True
                window._boot_refresh()

    return CompanionApplication().run(argv)
