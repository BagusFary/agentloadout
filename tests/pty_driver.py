"""Run one command on a pseudo-terminal in a separate, single-threaded process (N6).

pytest itself never forks a pty (forkpty inside a multi-threaded test process is fragile). Tests start this
script with subprocess, pass a JSON spec on stdin and get a JSON result on stdout:

  spec   {"argv": [...], "env": {...}, "answers": [[expect, send], ...], "timeout": 60,
          "cols": 80, "rows": 24, "stdout_pipe": false}
  result {"rc": int | null, "out": "...", "pipe_out": "...", "elapsed": seconds, "timed_out": bool}

Each answer waits until `expect` appears in the output after the previous answer, then sends `send` plus a
newline. Special sends: "^C" (Ctrl-C), "^D" (end of input, EOF), "" (just Enter). With stdout_pipe the
command's stdout goes to a pipe while stdin/stderr stay on the terminal (like `agentloadout | less`).
Nothing is ever sent unless an answer asks for it, so a command that waits for input waits until timeout.
"""
import fcntl
import json
import os
import pty
import select
import signal
import struct
import sys
import termios
import time

CONTROL = {"^C": b"\x03", "^D": b"\x04"}


def main():
    spec = json.load(sys.stdin)
    argv, env = spec["argv"], spec.get("env") or dict(os.environ)
    answers = [tuple(a) for a in spec.get("answers") or []]
    timeout = float(spec.get("timeout", 60))
    cols, rows = int(spec.get("cols", 80)), int(spec.get("rows", 24))
    pipe_r = pipe_w = None
    if spec.get("stdout_pipe"):
        pipe_r, pipe_w = os.pipe()
    pid, fd = pty.fork()
    if pid == 0:  # child: on the pty (session leader with a controlling terminal)
        try:
            fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
            if pipe_w is not None:
                os.dup2(pipe_w, 1)
                os.close(pipe_w)
                os.close(pipe_r)
            os.execvpe(argv[0], argv, env)
        finally:
            os._exit(127)
    if pipe_w is not None:
        os.close(pipe_w)
    out, pipe_out, pos, i = b"", b"", 0, 0
    start = time.time()
    timed_out = False
    fds = [fd] + ([pipe_r] if pipe_r is not None else [])
    while fds:
        if time.time() - start > timeout:
            timed_out = True
            os.kill(pid, signal.SIGKILL)
            break
        ready, _, _ = select.select(fds, [], [], 0.05)
        for r in ready:
            try:
                chunk = os.read(r, 4096)
            except OSError:
                chunk = b""
            if not chunk:
                fds.remove(r)
                continue
            if r == fd:
                out += chunk
            else:
                pipe_out += chunk
        if i < len(answers):
            expect, send = answers[i]
            hay = out + pipe_out
            k = hay.find(expect.encode(), pos)
            if k >= 0:
                pos = k + len(expect)
                time.sleep(0.05)  # let the prompt finish (getpass switches echo off first)
                os.write(fd, CONTROL.get(send, (send + "\n").encode()))
                i += 1
        if fd not in fds:
            break
    _, status = os.waitpid(pid, 0)
    try:
        os.close(fd)
    except OSError:
        pass
    rc = None if timed_out else os.waitstatus_to_exitcode(status)
    json.dump({"rc": rc, "out": out.decode(errors="replace"), "pipe_out": pipe_out.decode(errors="replace"),
               "elapsed": round(time.time() - start, 3), "timed_out": timed_out}, sys.stdout)


if __name__ == "__main__":
    main()
