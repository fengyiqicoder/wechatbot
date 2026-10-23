#!/usr/bin/env python3
"""将命令放入独立会话运行，避免随调用方进程组退出。"""

import argparse
import os
import subprocess


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("缺少要启动的命令")
    with open(args.log, "ab", buffering=0) as log:
        proc = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    print(proc.pid)


if __name__ == "__main__":
    main()
