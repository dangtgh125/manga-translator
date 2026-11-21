"""Script-based source language detection: ja / zh-Hans / zh-Hant / unknown.

Kana present -> Japanese. Han-only text is compared against a table of characters whose
simplified and traditional forms differ; when no distinguishing character is found the result
is flagged uncertain so the user is asked to confirm (PLAN.md §3).
"""

from __future__ import annotations

from dataclasses import dataclass

# Pairs (simplified, traditional) of frequent characters that differ between scripts.
_PAIRS = [
    ("这", "這"),
    ("个", "個"),
    ("们", "們"),
    ("说", "說"),
    ("来", "來"),
    ("时", "時"),
    ("会", "會"),
    ("对", "對"),
    ("过", "過"),
    ("还", "還"),
    ("没", "沒"),
    ("么", "麼"),
    ("为", "為"),
    ("动", "動"),
    ("发", "發"),
    ("国", "國"),
    ("东", "東"),
    ("车", "車"),
    ("门", "門"),
    ("见", "見"),
    ("开", "開"),
    ("关", "關"),
    ("长", "長"),
    ("无", "無"),
    ("当", "當"),
    ("点", "點"),
    ("后", "後"),
    ("两", "兩"),
    ("学", "學"),
    ("问", "問"),
    ("什", "什"),
    ("让", "讓"),
    ("从", "從"),
    ("里", "裡"),
    ("经", "經"),
    ("头", "頭"),
    ("几", "幾"),
    ("样", "樣"),
    ("间", "間"),
    ("电", "電"),
    ("话", "話"),
    ("请", "請"),
    ("谢", "謝"),
    ("马", "馬"),
    ("鸟", "鳥"),
    ("鱼", "魚"),
    ("书", "書"),
    ("写", "寫"),
    ("读", "讀"),
    ("听", "聽"),
    ("觉", "覺"),
    ("爱", "愛"),
    ("乐", "樂"),
    ("应", "應"),
    ("该", "該"),
    ("难", "難"),
    ("业", "業"),
    ("号", "號"),
    ("员", "員"),
    ("体", "體"),
    ("义", "義"),
    ("亲", "親"),
    ("术", "術"),
    ("历", "歷"),
    ("师", "師"),
    ("战", "戰"),
    ("气", "氣"),
    ("军", "軍"),
    ("医", "醫"),
    ("药", "藥"),
    ("钱", "錢"),
    ("买", "買"),
    ("卖", "賣"),
    ("刘", "劉"),
    ("张", "張"),
    ("陈", "陳"),
    ("赵", "趙"),
    ("齐", "齊"),
    ("风", "風"),
    ("云", "雲"),
    ("飞", "飛"),
    ("鸡", "雞"),
    ("龙", "龍"),
    ("凤", "鳳"),
    ("妈", "媽"),
    ("爷", "爺"),
    ("儿", "兒"),
    ("孙", "孫"),
    ("与", "與"),
    ("专", "專"),
    ("丽", "麗"),
    ("严", "嚴"),
    ("优", "優"),
    ("伤", "傷"),
    ("价", "價"),
    ("众", "眾"),
    ("传", "傳"),
    ("伦", "倫"),
    ("备", "備"),
    ("变", "變"),
    ("别", "別"),
    ("币", "幣"),
    ("帮", "幫"),
    ("边", "邊"),
    ("补", "補"),
    ("参", "參"),
    ("层", "層"),
    ("产", "產"),
    ("尝", "嘗"),
    ("称", "稱"),
    ("迟", "遲"),
    ("处", "處"),
    ("带", "帶"),
    ("单", "單"),
    ("导", "導"),
    ("灯", "燈"),
    ("递", "遞"),
    ("顶", "頂"),
    ("订", "訂"),
    ("断", "斷"),
    ("队", "隊"),
    ("饿", "餓"),
    ("罚", "罰"),
    ("纷", "紛"),
    ("负", "負"),
    ("刚", "剛"),
    ("广", "廣"),
    ("归", "歸"),
    ("过", "過"),
    ("汉", "漢"),
    ("护", "護"),
    ("华", "華"),
    ("欢", "歡"),
    ("击", "擊"),
    ("极", "極"),
    ("继", "繼"),
    ("奖", "獎"),
    ("惊", "驚"),
    ("旧", "舊"),
    ("剧", "劇"),
    ("决", "決"),
    ("块", "塊"),
    ("离", "離"),
    ("历", "曆"),
    ("练", "練"),
    ("灵", "靈"),
    ("录", "錄"),
    ("虑", "慮"),
    ("罗", "羅"),
    ("满", "滿"),
    ("梦", "夢"),
    ("纳", "納"),
    ("闹", "鬧"),
    ("农", "農"),
    ("拟", "擬"),
    ("骗", "騙"),
    ("凭", "憑"),
    ("齐", "齊"),
    ("签", "簽"),
    ("抢", "搶"),
    ("轻", "輕"),
    ("权", "權"),
    ("确", "確"),
    ("热", "熱"),
    ("认", "認"),
    ("荣", "榮"),
    ("伞", "傘"),
    ("杀", "殺"),
    ("伤", "傷"),
    ("声", "聲"),
    ("胜", "勝"),
    ("实", "實"),
    ("输", "輸"),
    ("属", "屬"),
    ("树", "樹"),
    ("双", "雙"),
    ("谁", "誰"),
    ("岁", "歲"),
    ("态", "態"),
    ("团", "團"),
    ("弯", "彎"),
    ("网", "網"),
    ("卫", "衛"),
    ("尉", "尉"),
    ("务", "務"),
    ("习", "習"),
    ("戏", "戲"),
    ("显", "顯"),
    ("乡", "鄉"),
    ("响", "響"),
    ("协", "協"),
    ("兴", "興"),
    ("选", "選"),
    ("寻", "尋"),
    ("压", "壓"),
    ("验", "驗"),
    ("阳", "陽"),
    ("页", "頁"),
    ("艺", "藝"),
    ("忆", "憶"),
    ("阴", "陰"),
    ("营", "營"),
    ("犹", "猶"),
    ("远", "遠"),
    ("运", "運"),
    ("杂", "雜"),
    ("赞", "讚"),
    ("择", "擇"),
    ("则", "則"),
    ("证", "證"),
    ("织", "織"),
    ("质", "質"),
    ("终", "終"),
    ("众", "眾"),
    ("转", "轉"),
    ("装", "裝"),
    ("状", "狀"),
    ("总", "總"),
    ("纵", "縱"),
    ("组", "組"),
    ("钻", "鑽"),
    ("尽", "盡"),
    ("坚", "堅"),
    ("检", "檢"),
    ("韩", "韓"),
    ("视", "視"),
    ("观", "觀"),
    ("现", "現"),
    ("规", "規"),
    ("觉", "覺"),
    ("览", "覽"),
    ("语", "語"),
    ("词", "詞"),
    ("诗", "詩"),
    ("试", "試"),
    ("调", "調"),
    ("谈", "談"),
    ("谓", "謂"),
    ("议", "議"),
    ("记", "記"),
    ("计", "計"),
    ("讨", "討"),
    ("训", "訓"),
    ("设", "設"),
    ("诉", "訴"),
]
_SIMPLIFIED_ONLY = {s for s, t in _PAIRS if s != t} - {t for _, t in _PAIRS}
_TRADITIONAL_ONLY = {t for s, t in _PAIRS if s != t} - {s for s, _ in _PAIRS}


def _is_kana(ch: str) -> bool:
    o = ord(ch)
    return 0x3040 <= o <= 0x30FF or 0x31F0 <= o <= 0x31FF or 0xFF66 <= o <= 0xFF9F


def _is_han(ch: str) -> bool:
    o = ord(ch)
    return 0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF or 0xF900 <= o <= 0xFAFF or 0x20000 <= o <= 0x2FA1F


@dataclass(frozen=True)
class LangGuess:
    lang: str  # ja | zh-Hans | zh-Hant | unknown
    confidence: float
    uncertain: bool


def detect_language(text: str) -> LangGuess:
    kana = sum(1 for ch in text if _is_kana(ch))
    han = sum(1 for ch in text if _is_han(ch))
    if kana > 0:
        conf = min(1.0, 0.6 + 0.1 * kana)
        return LangGuess("ja", conf, uncertain=kana < 2 and han > 4)
    if han == 0:
        return LangGuess("unknown", 0.0, uncertain=True)
    simp = sum(1 for ch in text if ch in _SIMPLIFIED_ONLY)
    trad = sum(1 for ch in text if ch in _TRADITIONAL_ONLY)
    if simp == 0 and trad == 0:
        # Han-only without distinguishing characters: could be ja (kanji-only), zh-Hans or zh-Hant.
        return LangGuess("unknown", 0.3, uncertain=True)
    # Han-only text is never certain: Japanese kanji-only lines look like Chinese. The page-level
    # vote (kana elsewhere) and the user's override settle it.
    if simp > trad:
        return LangGuess("zh-Hans", min(0.8, 0.45 + 0.1 * simp), uncertain=True)
    if trad > simp:
        return LangGuess("zh-Hant", min(0.8, 0.45 + 0.1 * trad), uncertain=True)
    return LangGuess("unknown", 0.3, uncertain=True)


def aggregate_page_language(guesses: list[LangGuess]) -> LangGuess:
    """Page-level vote weighted by confidence; mixed pages stay uncertain."""
    weights: dict[str, float] = {}
    for g in guesses:
        if g.lang == "unknown":
            continue
        weights[g.lang] = weights.get(g.lang, 0.0) + g.confidence
    if not weights:
        return LangGuess("unknown", 0.0, uncertain=True)
    best = max(weights, key=lambda k: weights[k])
    total = sum(weights.values())
    share = weights[best] / total if total else 0.0
    return LangGuess(best, share, uncertain=share < 0.8 or len(weights) > 1 and share < 0.9)
