"""NLU 修复工具"""

def chinese_num_to_int(s: str) -> int:
    """中文数字 → 整数: "一"→1, "十"→10, "十二"→12"""
    mapping = {
        "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
        "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
    }
    if len(s) == 1:
        return mapping.get(s, 0)
    # "十二" → 10+2, "二十" → 2*10+0, "十一" → 10+1
    if s == "十":
        return 10
    if s[0] == "十":
        return 10 + mapping.get(s[1], 0)
    if len(s) >= 2 and s[1] == "十":
        tens = mapping.get(s[0], 0)
        if len(s) == 2:
            return tens * 10
        return tens * 10 + mapping.get(s[2], 0)
    return 0
