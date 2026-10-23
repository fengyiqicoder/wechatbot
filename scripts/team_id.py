#!/usr/bin/env python3
"""找一个能签 WDA 的 Team ID：优先 Xcode 里已登录账号的付费 Team，其次免费 Personal Team。打印 Team ID，找不到打印空。"""
import plistlib
import subprocess


def xcode_teams() -> list[dict]:
    try:
        raw = subprocess.run(["defaults", "export", "com.apple.dt.Xcode", "-"], capture_output=True, check=True).stdout
        by_acct = plistlib.loads(raw).get("IDEProvisioningTeamByIdentifier") or {}
    except (subprocess.CalledProcessError, plistlib.InvalidFileException, ValueError):
        return []
    return [t for teams in by_acct.values() for t in teams if t.get("teamID")]


def main():
    teams = xcode_teams()
    teams.sort(key=lambda t: bool(t.get("isFreeProvisioningTeam")))   # 付费 Team 排前面
    print(teams[0]["teamID"] if teams else "")


if __name__ == "__main__":
    main()
