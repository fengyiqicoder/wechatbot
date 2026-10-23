from butler.apps import AppCatalog
from butler.memory import MemoryStore

APPS = {"com.atebits.Tweetie2": "X", "com.tencent.xin": "微信", "com.xingin.discover": "小红书", "net.whatsapp.WhatsApp": "WhatsApp"}


def test_refresh_and_resolve(tmp_path):
    c = AppCatalog(MemoryStore(tmp_path), lister=lambda: APPS)
    assert c.apps() == {}
    c.refresh()
    assert c.resolve("com.tencent.xin") == ("com.tencent.xin", [])
    assert c.resolve("微信") == ("com.tencent.xin", [])
    assert c.resolve("whatsapp") == ("net.whatsapp.WhatsApp", [])
    assert c.resolve("不存在") == (None, [])


def test_resolve_refreshes_on_miss(tmp_path):
    calls = []
    def lister():
        calls.append(1); return APPS
    c = AppCatalog(MemoryStore(tmp_path), lister=lister)
    assert c.resolve("X") == ("com.atebits.Tweetie2", []) and len(calls) == 1
