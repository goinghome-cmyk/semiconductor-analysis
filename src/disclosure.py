"""사업보고서 원문(XML)에서 필요한 섹션을 텍스트로 뽑아낸다.

DART 원문은 <SECTION-1> > <TITLE> + <SECTION-2> ... 구조입니다.
형식이 엄격하지 않아 HTML 파서(lxml)로 관대하게 읽습니다.
"""
from __future__ import annotations

import re
import warnings
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

from . import docrender

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

# 앱에서 보여줄 주요 섹션 (제목에 포함된 키워드로 찾음)
KEY_SECTIONS = {
    "사업의 내용": "사업의 내용",
    "경영진단(MD&A)": "이사의 경영진단 및 분석의견",
    "감사의견": "회계감사인의 감사의견",
}


@dataclass
class Section:
    title: str
    level: int
    text: str  # 구형 텍스트 (부문 실적 추출 파서가 사용)
    blocks: list = field(default_factory=list)  # 화면·AI용 구조화 블록 (문단/표)
    children: list["Section"] = field(default_factory=list)


def _clean(s: str) -> str:
    return re.sub(r"[ \t ]+", " ", s).strip()


def _node_text(node) -> str:
    """섹션 본문 -> 텍스트. 표는 '|' 구분 행으로, 하위 섹션은 제외."""
    lines = []
    for el in node.children:
        name = getattr(el, "name", None)
        if name is None:
            t = _clean(str(el))
            if t:
                lines.append(t)
            continue
        if name == "title" or re.fullmatch(r"section-\d", name):
            continue
        if el.find(_SECTION_RE):
            # <LIBRARY> 처럼 하위 섹션을 감싼 요소: 섹션 밖 본문만 재귀적으로
            inner = _node_text(el)
            if inner:
                lines.append(inner)
            continue
        if name == "table":
            for tr in el.find_all("tr"):
                cells = [_clean(c.get_text(" ")) for c in tr.find_all(["td", "th", "te", "tu"])]
                if any(cells):
                    lines.append(" | ".join(cells))
            lines.append("")
        else:
            t = _clean(el.get_text(" "))
            if t:
                lines.append(t)
    return "\n".join(lines).strip()


_SECTION_RE = re.compile(r"^section-\d$")


def _walk(node, level: int) -> list[Section]:
    """node 안의 section-{level} 중, 중간에 다른 섹션 없이 속한 것들 (LIBRARY 등으로 감싸진 경우 포함)."""
    out = []
    for child in node.find_all(re.compile(rf"^section-{level}$")):
        parent_sec = child.find_parent(_SECTION_RE)
        if level > 1 and parent_sec is not node:
            continue
        if level == 1 and parent_sec is not None:
            continue
        title_el = child.find("title", recursive=False)
        title = _clean(title_el.get_text(" ")) if title_el else "(제목 없음)"
        sec = Section(title=title, level=level, text=_node_text(child), blocks=docrender.blocks(child))
        sec.children = _walk(child, level + 1)
        out.append(sec)
    return out


_SEC1_RE = re.compile(r"<SECTION-1\b.*?</SECTION-1>", re.S)
_SEC2_RE = re.compile(r"<SECTION-2\b.*?</SECTION-2>", re.S)
_TITLE_RE = re.compile(r"<TITLE[^>]*>(.*?)</TITLE>", re.S)

# 앱이 쓰는 섹션만 (대목차 키워드, 재무에 관한 사항 안에서는 '주석'만)
APP_SECTIONS = ("사업의 내용", "경영진단", "감사인", "재무에 관한 사항")


def _title_of(chunk: str) -> str:
    m = _TITLE_RE.search(chunk[:2000])
    return re.sub(r"<[^>]+>|\s+", "", m.group(1)) if m else ""


def _prefilter(xml_text: str, keep: tuple[str, ...]) -> str:
    """원문이 수 MB라 파싱이 느리다. 필요한 대목차만 문자열로 잘라 붙인다.
    '재무에 관한 사항'은 재무제표 표 자체는 빼고 주석 섹션만 남긴다(주석이 없으면 전체)."""
    kept = []
    for chunk in _SEC1_RE.findall(xml_text):
        title = _title_of(chunk)
        if not any(k.replace(" ", "") in title for k in keep):
            continue
        if "재무에관한사항" in title:
            notes = [c for c in _SEC2_RE.findall(chunk) if "주석" in _title_of(c)]
            # 연결 주석이 있으면 별도 주석은 생략. 자회사가 없는 회사는 연결 주석 자리에
            # '작성대상에 해당하지 않습니다' 한 줄만 있으므로(리노공업 등) 그때는 별도 주석을 쓴다.
            consolidated = [c for c in notes if "연결" in _title_of(c) and len(c) > 5000]
            notes = consolidated or [c for c in notes if "연결" not in _title_of(c)] or notes
            if notes:
                head = chunk[:chunk.find("<SECTION-2")] if "<SECTION-2" in chunk else chunk
                chunk = head + "".join(notes) + "</SECTION-1>"
        kept.append(chunk)
    return "<BODY>" + "".join(kept) + "</BODY>" if kept else xml_text


def parse_sections(xml_text: str, keep: tuple[str, ...] | None = None) -> list[Section]:
    """keep 을 주면 제목에 그 키워드가 있는 대목차만 파싱(속도). None 이면 전체."""
    if keep:
        xml_text = _prefilter(xml_text, keep)
    soup = BeautifulSoup(xml_text, "lxml")
    root = soup.find("body") or soup
    # 최상위 section-1 이 body 바로 아래가 아닐 수 있으므로 가장 가까운 부모를 찾는다
    if root.find(re.compile(r"^section-1$")) is None:
        return []
    return _walk(root, 1)


def full_text(sec: Section) -> str:
    parts = [sec.text] if sec.text else []
    for c in sec.children:
        parts.append(f"\n## {c.title}\n{full_text(c)}")
    return "\n".join(parts).strip()


def full_blocks(sec: Section) -> list:
    """섹션 + 하위 섹션 블록 (하위 섹션 제목은 '## ' 문단으로)."""
    out = list(sec.blocks)
    for c in sec.children:
        out.append(("p", f"## {c.title}"))
        out.extend(full_blocks(c))
    return out


def find_section(sections: list[Section], keyword: str) -> Section | None:
    for s in sections:
        if keyword.replace(" ", "") in s.title.replace(" ", ""):
            return s
        hit = find_section(s.children, keyword)
        if hit:
            return hit
    return None


def main_document(files: dict[str, str]) -> str:
    """zip 안의 여러 파일 중 본문(가장 긴 이름 없는 파일)을 고른다."""
    main = [n for n in files if re.fullmatch(r"\d{14}\.xml", n)]
    if main:
        return files[main[0]]
    return max(files.values(), key=len) if files else ""


def split_notes(text: str) -> list[tuple[str, str]]:
    """재무제표 주석 텍스트를 '1. 일반사항', '2. ...' 번호 순서대로 나눈다."""
    notes: list[tuple[str, str]] = []
    expected = 1
    title, buf = "머리말", []
    for line in text.splitlines():
        # 다음 번호(또는 제목이 본문에 붙어 누락된 경우를 위해 +1, +2)로 시작하는 짧은 줄이 제목.
        # '25.2' 같은 숫자는 제외하려고 번호 뒤 첫 글자는 숫자가 아니어야 한다.
        m = re.match(r"^\s*(\d{1,2})\s*[.)]\s*([^\d\s].{0,60})$", line.rstrip())
        if m and "|" not in line and expected <= int(m.group(1)) <= expected + 2:
            if buf:
                notes.append((title, "\n".join(buf).strip()))
            n = int(m.group(1))
            title, buf = f"{n}. {m.group(2).strip()}", []
            expected = n + 1
            continue
        buf.append(line)
    if buf:
        notes.append((title, "\n".join(buf).strip()))
    return [(t, b) for t, b in notes if b]
