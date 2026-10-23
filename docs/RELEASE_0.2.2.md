# xCAL Community 0.2.2 - installation isolation patch

The program is now named **xCAL Community**, including its executable
`xCAL Community.exe`, window title, Windows application identity and shortcuts.

Install with `xCAL-Community-Setup-0.2.2.exe`. The installer always uses
`%LOCALAPPDATA%\Programs\xCAL Community`, ignores previously remembered install
folders and refuses a different destination. This prevents a previously shared
regular xCAL installation path from being reused. Community's user data remains
in its existing separate app-data directory.

If both older shortcuts opened the same program, install this patch and open
**xCAL Community** from the Start menu. This patch does not uninstall, delete or
repair files in regular xCAL's folder. Do not run an old uninstaller pointing at
the shared folder; regular xCAL may need repair from its own installer separately.

The release remains an unsigned Windows beta. Google approval and production
acceptance status are unchanged. See the [tester guide](BETA_QUICKSTART.md).
