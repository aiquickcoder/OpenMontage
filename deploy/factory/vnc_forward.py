#!/usr/bin/env python3
"""Reach the server's noVNC (127.0.0.1:6081 on 155) when sshd forbids TCP forwarding.

Each local connection to localhost:6081 runs `ssh root@155 nc 127.0.0.1 6081` and pipes
bytes both ways. No sshd change and nothing is exposed publicly: it's an ordinary ssh
exec session per connection.

    python3 deploy/factory/vnc_forward.py      # then open http://localhost:6081/vnc.html
"""

import os
import socket
import subprocess
import sys
import threading

HOST = os.environ.get("FACTORY_HOST", "root@155.212.156.162")
KEY = os.path.expanduser(os.environ.get("FACTORY_KEY", "~/.ssh/claude_quillon_pilot"))
PORT = int(os.environ.get("FACTORY_VNC_PORT", "6081"))
LOCAL_PORT = int(os.environ.get("FACTORY_VNC_LOCAL_PORT", PORT))


def ssh_to_socket(proc: subprocess.Popen, conn: socket.socket) -> None:
    try:
        while data := proc.stdout.read1(65536):
            conn.sendall(data)
    except OSError:
        pass
    finally:
        try:
            conn.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def socket_to_ssh(conn: socket.socket, proc: subprocess.Popen) -> None:
    try:
        while data := conn.recv(65536):
            proc.stdin.write(data)
            proc.stdin.flush()
    except OSError:
        pass
    finally:
        try:
            proc.stdin.close()
        except OSError:
            pass


def handle(conn: socket.socket) -> None:
    proc = subprocess.Popen(
        ["ssh", "-q", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", "-i", KEY, HOST, f"nc 127.0.0.1 {PORT}"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    back = threading.Thread(target=ssh_to_socket, args=(proc, conn), daemon=True)
    back.start()
    socket_to_ssh(conn, proc)
    back.join(timeout=30)
    conn.close()
    proc.terminate()


def main() -> int:
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", LOCAL_PORT))
    srv.listen(16)
    print(f"noVNC: http://localhost:{LOCAL_PORT}/vnc.html  (Ctrl+C to stop)", flush=True)
    try:
        while True:
            conn, _ = srv.accept()
            threading.Thread(target=handle, args=(conn,), daemon=True).start()
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
