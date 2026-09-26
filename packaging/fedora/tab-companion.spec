# SPDX-License-Identifier: MIT
%{!?app_version:%global app_version 1.4.2}
%{!?app_release:%global app_release 1.x810.4}
Name:           tab-companion
Version:        %{app_version}
Release:        %{app_release}%{?dist}
Summary:        GTK companion app for Samsung Galaxy tablets
License:        MIT
BuildArch:      noarch
Source0:        tab-companion-x810.tar.gz
Requires:       python3 >= 3.10
Requires:       python3-gobject
Requires:       python3-dbus
Requires:       gtk4
Requires:       libadwaita
Requires:       glib2
Requires:       polkit
Requires:       systemd
Requires:       dbus
Requires:       gnome-shell
BuildRequires:  tar
BuildRequires:  findutils
BuildRequires:  python3

%description
Tab Companion configures Samsung S Pen, cover-keyboard and tablet hardware
features. This X810 package also includes the X810 boot-switch integration.

%prep

%build

%install
mkdir -p %{buildroot}
tar -xzf %{SOURCE0} -C %{buildroot}
mkdir -p %{_builddir}
find %{buildroot} \( -type f -o -type l \) ! -path '%{buildroot}/usr/share/licenses/tab-companion/LICENSE' -print | sed "s|^%{buildroot}||" | sort > %{_builddir}/tab-companion.files

%post
# Remove the legacy no-password boot-switch exception created by older builds.
rm -f /etc/polkit-1/rules.d/49-tab-companion-boot-switch.rules
if command -v glib-compile-schemas >/dev/null 2>&1; then
    glib-compile-schemas /usr/share/glib-2.0/schemas || :
fi
if command -v udevadm >/dev/null 2>&1; then
    udevadm control --reload-rules || :
fi

%postun
if command -v glib-compile-schemas >/dev/null 2>&1; then
    glib-compile-schemas /usr/share/glib-2.0/schemas || :
fi
if command -v udevadm >/dev/null 2>&1; then
    udevadm control --reload-rules || :
fi

%files -f %{_builddir}/tab-companion.files
%defattr(-,root,root,-)
%license %{_datadir}/licenses/tab-companion/LICENSE
