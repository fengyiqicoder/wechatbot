from butler.extract import list_items, text_blocks


def el(tag, label, rect, value=""):
    return {"tag": tag, "name": label, "label": label, "value": value, "rect": list(rect)}


ELS = [el("Cell", "Max Feng。 @Feng_YQ", (0, 92, 414, 82)), el("StaticText", "Max Feng", (64, 104, 72, 20)),
       el("StaticText", "@Feng_YQ", (64, 124, 75, 20)), el("Cell", "fengyq。 @fengyq159357", (0, 174, 414, 82)),
       el("StaticText", "fengyq", (64, 186, 53, 20)), el("Key", "q", (2, 602, 41, 49)),
       el("StaticText", "标题", (10, 60, 50, 20)), el("Button", "私信", (9, 686, 194, 40))]


def test_text_blocks_reading_order_without_keys():
    assert [b["text"] for b in text_blocks(ELS)] == ["标题", "Max Feng", "@Feng_YQ", "fengyq"]


def test_list_items_group_children():
    items = list_items(ELS)
    assert len(items) == 2 and items[0]["index"] == 1
    assert items[0]["texts"] == ["Max Feng", "@Feng_YQ"] and items[1]["texts"] == ["fengyq"]


def test_list_items_supports_telegram_style_rows():
    """Swiftgram/Telegram 的会话行是通栏 Other + 换行分隔，没有 Cell，也要能提取。"""
    rows = [el("Other", "Swiftgram", (0, 0, 414, 896)),                    # 根容器，非行
            el("Other", "LIHAICloud\n3 条未读消息", (0, 165, 414, 76)),
            el("Other", "Pavel Durov\n73 条未读消息", (0, 317, 414, 76)),
            el("Image", "Navigation/Search", (355, 815, 30, 30))]
    items = list_items(rows)
    assert [i["label"] for i in items] == ["LIHAICloud", "Pavel Durov"]
    assert items[0]["texts"] == ["LIHAICloud", "3 条未读消息"] and items[0]["index"] == 1


def test_text_blocks_reads_self_drawn_message_bubbles():
    """Telegram/Swiftgram 的消息气泡是带文字的 Other，必须能当正文读出来；容器不算。"""
    els = [el("Application", "Swiftgram", (0, 0, 414, 896)),
           el("Other", "整个会话容器", (0, 0, 414, 800)),          # 含子文本 → 不算正文
           el("Other", "早上好", (60, 200, 200, 40)),
           el("Other", "收到，谢谢", (150, 250, 200, 40)),
           el("Key", "Q", (2, 602, 41, 49))]
    got = [b["text"] for b in text_blocks(els)]
    assert got == ["早上好", "收到，谢谢"]
