"""The stuck-port owner and the duplicate-process list off Windows (2026-10-08 OS audit).

Both returned None on Linux and macOS without psutil, so the console's stuck-port reclaim never fired
and the "one OS process per job" watchdog check never ran. These pin the pure parsers on fake /proc
trees and sample `ps`/`lsof` output, so they run on any OS.
"""

from __future__ import annotations

import os

import pytest

from cherrypick.orchestrator import util, watchdog

# /proc/net/tcp: 127.0.0.1:5070 (0x13CE) LISTEN with inode 4242; :5070 ESTABLISHED; :8080 LISTEN.
PROC_NET_TCP = """\
  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode
   0: 0100007F:13CE 00000000:0000 0A 00000000:00000000 00:00000000 00000000  1000        0 4242 1
   1: 0100007F:13CE 0100007F:9C40 01 00000000:00000000 00:00000000 00000000  1000        0 5151 1
   2: 00000000:1F90 00000000:0000 0A 00000000:00000000 00:00000000 00000000  1000        0 6363 1
"""


def test_listen_inodes_takes_only_the_listening_socket_on_that_port():
    assert util.listen_inodes(PROC_NET_TCP, 5070) == {"4242"}
    assert util.listen_inodes(PROC_NET_TCP, 8080) == {"6363"}
    assert util.listen_inodes(PROC_NET_TCP, 9999) == set()
    assert util.listen_inodes("", 5070) == set()


@pytest.mark.skipif(os.name == "nt", reason="fd symlinks")
def test_linux_port_owner_finds_the_process_holding_that_socket(tmp_path):
    (tmp_path / "net").mkdir()
    (tmp_path / "net" / "tcp").write_text(PROC_NET_TCP)
    for pid, target in (("100", "socket:[5151]"), ("200", "socket:[4242]")):
        fd = tmp_path / pid / "fd"
        fd.mkdir(parents=True)
        os.symlink(target, fd / "3")
    assert util._linux_port_owner(5070, proc=str(tmp_path)) == 200
    assert util._linux_port_owner(9999, proc=str(tmp_path)) is None


def test_lsof_output_gives_the_first_pid_or_none():
    assert util.first_pid("812\n813\n") == 812
    assert util.first_pid("") is None
    assert util.first_pid("lsof: WARNING\n") is None


def _proc(root, pid, argv, ppid, comm="python3"):
    d = root / str(pid)
    d.mkdir()
    (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")
    (d / "stat").write_text(f"{pid} ({comm}) S {ppid} {pid} {pid} 0 -1 4194304")


def test_proc_listing_keeps_python_and_node_with_their_parents(tmp_path):
    _proc(tmp_path, 10, ["/home/u/.venv/bin/python", "-m", "cherrypick.flies.live_loop", "--live"], 1)
    _proc(tmp_path, 11, ["/usr/bin/node", "dist/index.js"], 10, comm="node")
    _proc(tmp_path, 12, ["/bin/bash", "-c", "x"], 1, comm="bash")
    _proc(tmp_path, 13, ["python3", "x"], 1, comm="py (weird) name")  # a ")" in the command name
    (tmp_path / "14").mkdir()  # a kernel thread: no cmdline
    (tmp_path / "self").mkdir()
    rows = sorted(watchdog._list_processes_proc(str(tmp_path)), key=lambda r: r["pid"])
    assert rows == [
        {"pid": 10, "ppid": 1, "cmd": "/home/u/.venv/bin/python -m cherrypick.flies.live_loop --live"},
        {"pid": 11, "ppid": 10, "cmd": "/usr/bin/node dist/index.js"},
        {"pid": 13, "ppid": 1, "cmd": "python3 x"},
    ]
    assert watchdog._list_processes_proc(str(tmp_path / "absent")) is None


def test_ps_output_is_parsed_and_filtered():
    text = (
        "  501     1 /opt/homebrew/bin/python3.13 -m cherrypick.flies.live_loop --live\n"
        "  502   501 /opt/homebrew/bin/node dist/index.js\n"
        "  503     1 /bin/zsh -l\n"
    )
    assert watchdog.parse_ps(text) == [
        {"pid": 501, "ppid": 1, "cmd": "/opt/homebrew/bin/python3.13 -m cherrypick.flies.live_loop --live"},
        {"pid": 502, "ppid": 501, "cmd": "/opt/homebrew/bin/node dist/index.js"},
    ]


def test_two_proc_rows_of_one_live_job_are_a_duplicate(tmp_path):
    argv = ["/v/bin/python", "-m", "cherrypick.flies.live_loop", "--live"]
    _proc(tmp_path, 20, argv, 1)
    _proc(tmp_path, 21, argv, 1)
    rows = watchdog._list_processes_proc(str(tmp_path))
    sig = {"flies-live": watchdog._norm_cmd("-m cherrypick.flies.live_loop --live")}
    assert watchdog._duplicate_groups(rows, sig) == {"flies-live": [20, 21]}
