# SPDX-License-Identifier: MIT

def main(argv):
    if "--tui" in argv:
        from .tui import main as tui_main

        tui_argv = [arg for arg in argv if arg != "--tui"]
        return tui_main(tui_argv[1:])

    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gio

    from . import APP_ID
    from .window import CompanionWindow

    class CompanionApplication(Adw.Application):
        def __init__(self):
            super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
            self.connect("activate", self._activate)

        def _activate(self, _app):
            window = self.props.active_window
            if window is None:
                window = CompanionWindow(self)
            window.present()

    return CompanionApplication().run(argv)
