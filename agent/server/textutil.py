"""한국어·부품 규격을 함께 다루는 단순 토큰화와 BM25."""
import math, re
from collections import Counter

SPEC = re.compile(r"[A-Za-z]+\d+(?:[×xX*]\d+)?|\d+(?:\.\d+)?\s?mm|\d+")
WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|[가-힣]+|\d+")
STOP = {"이", "그", "저", "것", "거", "수", "좀", "어떻게", "뭐", "왜", "해", "돼", "하는", "있어", "해야", "이거", "저거", "그거", "여기", "거기"}


# 조립 동사는 활용형이 많아서 같은 뿌리로 묶는다 (조여/조이기/조임 → 조이)
STEMS = {"조여": "조이", "조이": "조이", "조임": "조이", "끼워": "끼우", "끼우": "끼우", "끼움": "끼우",
         "넣어": "넣", "넣기": "넣", "넣고": "넣", "맞춰": "맞추", "맞추": "맞추", "돌려": "돌리", "돌리": "돌리",
         "붙여": "붙이", "붙이": "붙이", "띄워": "띄우", "띄우": "띄우", "떠있": "띄우", "떠 ": "띄우",
         "고정": "고정", "묶어": "묶", "묶기": "묶", "꽂아": "꽂", "꽂": "꽂", "향해": "향하", "향하": "향하"}


# 현장에서 섞어 부르는 이름 (질문에 왼쪽 말이 있으면 오른쪽 말도 찾는다)
SYNONYMS = {"커넥터": "xt30 usb-c 선", "전원선": "xt30", "케이블": "선 xt30 usb-c", "핀": "링크 구멍",
            "틈": "떠 간격", "간격": "틈 떠", "나사": "볼트", "베어링": "6709", "모터": "액추에이터"}


def expand(query: str) -> str:
    return query + " " + " ".join(v for k, v in SYNONYMS.items() if k in query)


def tokens(text: str) -> list[str]:
    t = text.lower().replace("×", "x")
    out = []
    for m in SPEC.finditer(t):
        out.append(m.group(0).replace(" ", ""))
    for w in WORD.findall(t):
        if w in STOP:
            continue
        if re.match(r"[가-힣]", w):
            out.append(w)
            for pre, stem in STEMS.items():
                if w.startswith(pre.strip()):
                    out.append("@" + stem)
                    break
            if len(w) >= 2:  # 조사 붙은 말도 맞도록 2글자 조각
                out += [w[i:i + 2] for i in range(len(w) - 1)]
        else:
            out.append(w)
    return out


def similarity(a: str, b: str) -> float:
    ta, tb = set(tokens(a)), set(tokens(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


class BM25:
    def __init__(self, docs: list[str], k1=1.4, b=0.75):
        self.toks = [tokens(d) for d in docs]
        self.k1, self.b = k1, b
        self.avg = sum(len(t) for t in self.toks) / max(len(self.toks), 1)
        df = Counter()
        for t in self.toks:
            df.update(set(t))
        n = len(self.toks)
        self.idf = {w: math.log(1 + (n - c + 0.5) / (c + 0.5)) for w, c in df.items()}
        self.tf = [Counter(t) for t in self.toks]

    def scores(self, query: str) -> list[float]:
        q = list(dict.fromkeys(tokens(query)))  # 같은 말이 두 번 세어지지 않게
        out = []
        for tf, tk in zip(self.tf, self.toks):
            s = 0.0
            for w in q:
                if w in tf:
                    f = tf[w]
                    s += self.idf[w] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * len(tk) / self.avg))
            out.append(s)
        return out


def brief(s: str, n: int = 48) -> str:
    """소리로 읽을 말: 핵심만 짧게. 첫 문장만 쓰고, 그래도 길면 n자 안에서 쉼표·띄어쓰기 자리에서 자른다.
    자세한 설명·이유·출처는 화면(패널)에 그대로 둔다."""
    s = re.sub(r"\s+", " ", (s or "")).strip()
    s = re.sub(r"^(설계자 설명으로는|이전 작업자 팁이에요\.|주의할 점이에요\.)[, ]*", "", s)
    if len(s) <= n:
        return s
    m = re.match(r"(.+?[.!?。])\s", s + " ")
    first = m.group(1) if m else s
    if len(first) <= n * 1.6:          # 한 문장은 조금 길어도 통째로 (중간에서 자르면 더 어색하다)
        return first
    cut = max(first.rfind(",", 0, n), first.rfind(" ", 0, n))
    return (first[:cut] if cut > n // 2 else first[:n]).rstrip(" ,") + "."
