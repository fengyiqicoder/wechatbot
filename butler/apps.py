"""App 目录：bundle id ↔ 显示名，来自 pymobiledevice3，缓存到 memory/apps.json。"""
from __future__ import annotations
from butler.names import match


def default_lister() -> dict:
    from pymobiledevice3.lockdown import create_using_usbmux
    from pymobiledevice3.services.installation_proxy import InstallationProxyService
    ld = create_using_usbmux()
    out = {}
    for kind in ("User", "System"):
        for b, v in InstallationProxyService(ld).get_apps(application_type=kind).items():
            out[b] = v.get("CFBundleDisplayName") or v.get("CFBundleName") or b
    return out


def device_info() -> dict | None:
    try:
        from pymobiledevice3.lockdown import create_using_usbmux
        ld = create_using_usbmux()
        return {"name": ld.get_value(key="DeviceName"), "ios": ld.product_version, "udid": ld.udid}
    except Exception:  # noqa: BLE001
        return None


class AppCatalog:
    def __init__(self, memory, lister=None):
        self.memory, self.lister = memory, lister or default_lister

    def apps(self) -> dict:
        return self.memory.load_apps()

    def refresh(self) -> dict:
        try:
            apps = self.lister()
        except Exception:  # noqa: BLE001 — 设备不在线时保留旧目录
            return self.apps()
        self.memory.save_apps(apps)
        return apps

    def name_of(self, bundle: str | None) -> str | None:
        """bundle id → 显示名（镜像窗口用）；不认识就原样返回。"""
        if not bundle:
            return None
        name = self.apps().get(bundle)
        if name:
            return name
        from butler.paths import APP_BUNDLES, APP_NAMES
        for key, b in APP_BUNDLES.items():
            if b == bundle:
                return APP_NAMES.get(key, bundle)
        return {"com.apple.springboard": "桌面"}.get(bundle, bundle)

    def resolve(self, target: str) -> tuple[str | None, list[str]]:
        apps = self.apps()
        if target in apps:
            return target, []
        r = self._by_name(target, apps)
        if r[0] or r[1]:
            return r
        apps = self.refresh()
        if target in apps:
            return target, []
        return self._by_name(target, apps)

    def _by_name(self, target, apps) -> tuple[str | None, list[str]]:
        bundles = list(apps.keys())
        m = match(target, [apps[b] for b in bundles])
        if m.tier == "none":
            return None, []
        if len(m.hits) == 1:
            return bundles[m.hits[0]], []
        return None, [bundles[i] for i in m.hits]
