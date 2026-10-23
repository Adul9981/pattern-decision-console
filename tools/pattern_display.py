"""Shared Chinese display names for the pattern intelligence surfaces."""

from __future__ import annotations

DISPLAY_NAMES = {
    "A1_V型极值反转": "极深V型反转",
    "A2_中位U型反转": "中位U型反转",
    "A3_折价修复": "高位回撤修复",
    "A4_下狗整场反转": "整场逆袭反转",
    "A5_W型双底": "W型双底反转",
    "A6_反弹确认": "深水反弹确认",
    "A7_强强对话错杀": "强强错杀反转",
    "B1_尾盘崩塌": "尾盘急跌归零",
    "B2_死亡螺旋": "整场持续坠落",
    "B3_假反弹后归零": "假反弹后归零",
    "B4_低开阴跌": "低位持续阴跌",
    "B4_直线阴跌": "持续阴跌",
    "C1_中位震荡": "中线反复拉锯",
    "C2_五五开开局碾压": "均势后单边碾压",
    "C2_早期缩距/热门确立": "低位追赶后确立",
    "热门全程压制": "热门全程压制",
    "未知": "暂未归类",
}

SHORT_NAMES = {
    "A1": "极深V型反转",
    "A2": "中位U型反转",
    "A3": "高位回撤修复",
    "A4": "整场逆袭反转",
    "A5": "W型双底反转",
    "A6": "深水反弹确认",
    "A7": "强强错杀反转",
    "B1": "尾盘急跌归零",
    "B2": "整场持续坠落",
    "B3": "假反弹后归零",
    "B4": "持续阴跌",
    "C1": "中线反复拉锯",
    "C2": "均势后单边碾压",
}

CODE_PHRASES = {
    "B3 假反弹后归零": "假反弹后归零",
    "B4 阴跌": "持续阴跌",
}

FAMILY_NAMES = {
    "反转": "反转结构",
    "崩塌": "坠落结构",
    "确立": "确立结构",
    "震荡": "拉锯结构",
    "压制": "压制结构",
    "观察": "观察结构",
}

LEVEL_NAMES = {"game": "小局", "series": "整场"}


def display_name(label: str) -> str:
    """Translate one internal taxonomy key into its user-facing Chinese name."""
    return DISPLAY_NAMES.get(str(label), str(label).replace("_", " · "))


def display_text(value: object) -> str:
    """Remove internal short codes from prose while preserving the meaning."""
    text = str(value)
    for internal, shown in sorted(DISPLAY_NAMES.items(), key=lambda item: len(item[0]), reverse=True):
        text = text.replace(internal, shown)
    for phrase, shown in CODE_PHRASES.items():
        text = text.replace(phrase, shown)
    for short, shown in SHORT_NAMES.items():
        text = text.replace(short, shown)
    return text


def family_name(family: str) -> str:
    """Return a readable family label without leaking internal taxonomy codes."""
    return FAMILY_NAMES.get(str(family or ""), "观察结构")


def level_name(level: str) -> str:
    return LEVEL_NAMES.get(str(level), "未知层级")
