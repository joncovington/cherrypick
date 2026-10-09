"""The desktop channel off Windows (2026-10-08 OS audit): it was Windows-only and silently skipped on a
Mac or a Linux desktop, so a desktop-only setup heard nothing there."""

from __future__ import annotations

from cherrypick.notify import notifier


def _which(found):
    return lambda name: f"/usr/bin/{name}" if name in found else None


def test_macos_uses_osascript_with_quoted_applescript_literals():
    argv = notifier.desktop_argv("darwin", "WARN", 'cherrypick: "on battery"', "62% \\ 1:10 left", _which(()))
    assert argv == [
        "osascript",
        "-e",
        'display notification "62% \\\\ 1:10 left" with title "cherrypick: \\"on battery\\""',
    ]


def test_linux_uses_notify_send_and_warnings_are_critical_urgency():
    argv = notifier.desktop_argv("linux", "CRITICAL", "cherrypick: t", "m", _which({"notify-send"}))
    assert argv == ["notify-send", "-u", "critical", "cherrypick: t", "m"]
    assert notifier.desktop_argv("linux", "INFO", "t", "m", _which({"notify-send"}))[2] == "normal"


def test_a_host_without_a_notifier_is_skipped_not_failed(monkeypatch):
    assert notifier.desktop_argv("linux", "INFO", "t", "m", _which(())) is None
    assert notifier.desktop_argv("freebsd13", "INFO", "t", "m", _which({"notify-send"})) is None


def test_the_channel_launches_the_command_off_windows(monkeypatch):
    launched = []
    monkeypatch.setattr(notifier.os, "name", "posix")
    monkeypatch.setattr(notifier.sys, "platform", "linux")
    monkeypatch.setattr(notifier.shutil, "which", _which({"notify-send"}))
    monkeypatch.setattr(notifier.subprocess, "Popen", lambda argv, **kw: launched.append(argv))
    n = notifier.Notifier({"channels": ["log", "desktop"]})
    assert n._push_desktop("WARN", "On battery", "62% left") == {"ok": True}
    assert launched and launched[0][0] == "notify-send" and launched[0][-1] == "62% left"
