"""TTS용 숫자 읽기: 숫자를 한글로 바꿔 둔다 (Supertonic은 '4번'을 '세 번'처럼 잘못 읽는다).
개수·시간처럼 고유어로 세는 것(세 개, 한 시간)과 번호·수치(사 번, 이 점 오 밀리)를 나눈다."""
import re

SINO = "영일이삼사오육칠팔구"
NATIVE = {1: "한", 2: "두", 3: "세", 4: "네", 5: "다섯", 6: "여섯", 7: "일곱", 8: "여덟", 9: "아홉", 10: "열",
          20: "스무"}
NATIVE_TENS = {10: "열", 20: "스물", 30: "서른", 40: "마흔", 50: "쉰", 60: "예순", 70: "일흔", 80: "여든", 90: "아흔"}
NATIVE_COUNTERS = ("개", "시간", "명", "군데", "바퀴", "가지", "줄", "장", "쌍", "살")   # 고유어로 세는 말


def sino(n: int) -> str:
    if n == 0:
        return "영"
    out = ""
    for unit, name in ((10000, "만"), (1000, "천"), (100, "백"), (10, "십")):
        q, n = divmod(n, unit)
        if q:
            out += ("" if q == 1 and unit != 10000 else sino(q)) + name
    return out + (SINO[n] if n else "")


def native(n: int) -> str:
    if n in NATIVE and n != 20:
        return NATIVE[n]
    if n == 20:
        return "스무"
    if 10 < n < 100:
        t, o = divmod(n, 10)
        return NATIVE_TENS[t * 10] + (NATIVE.get(o, "") if o else "")
    return sino(n)


def number_word(num: str, after: str) -> str:
    if "." in num:
        a, b = num.split(".", 1)
        return sino(int(a)) + " 점 " + " ".join(SINO[int(c)] for c in b)
    n = int(num)
    if after.startswith(NATIVE_COUNTERS) and 0 < n < 100:
        return native(n)
    return sino(n)


def ko_numbers(text: str) -> str:
    t = re.sub(r"\bM(\d+)", lambda m: "엠 " + sino(int(m.group(1))), text)            # M3 → 엠 삼
    t = re.sub(r"(\d)\s*mm\b", r"\1 밀리", t)
    t = re.sub(r"(\d)\s*A\b", r"\1 암페어", t)
    t = re.sub(r"(\d+(?:\.\d+)?)(\s*)(\S*)", lambda m: number_word(m.group(1), m.group(3)) + m.group(2) + m.group(3), t)
    return t
