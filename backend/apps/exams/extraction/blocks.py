"""Segmentação do laudo em blocos de exame.

Cada exame costuma começar numa linha de título seguida de `Material:` e terminar
em `Liberado por`. Blocos pequenos dão ao LLM entradas focadas (mais precisão) e
tornam a checagem de "o valor está no texto?" barata. Sem âncoras, cai para
blocos por página.

Há ainda o layout "Softl@b" (cabeçalho `Pedido:`; exames separados por linha pontilhada, por
`DATA DA COLETA:` ou só pela linha de título seguinte): ver `_split_softlab_blocks`.

Há um segundo layout, o da "ficha" (sem `Material:`): cada exame começa numa linha de
título, traz o cabeçalho de colunas `RESULTADO` / `VALORES DE REFERÊNCIA` e termina na linha
`RECEBIDO/COLETADO EM` (seguida de `Assinatura digital avançada`). Só é usado quando o laudo
não tem nenhuma âncora `Material:`; com âncoras, vale sempre o caminho original.
"""

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date

from .linearize import PageText
from .softlab_lines import (
    is_closer,
    is_footnote,
    is_meta,
    is_percent_variant,
    is_title_only,
    parse_row,
    reference_header,
    starts_method_or_material,
)
from .text import compact, fold, normalize_name

_ANCHOR = re.compile(r"^material\s*[:|]")
_END = re.compile(
    r"liberad[oa]\s+(?:por|eletronicamente)|assinado\s+(?:digitalmente|eletronicamente)"
)
_METHOD = re.compile(r"^m[eé]todos?\s*:\s*\|?\s*(.+)$", re.IGNORECASE)
_SPECIMEN = re.compile(r"^material\s*:\s*\|?\s*(.+)$", re.IGNORECASE)
_COLLECTED = re.compile(
    r"data (?:da )?coleta[\s:|]*(\d{2}/\d{2}/\d{4})(?:\s*[-–]?\s*(\d{2}:\d{2}(?::\d{2})?))?",
    re.IGNORECASE,
)
# Segunda linha de um nome que não coube na primeira (`GLOBULINA LIGADORA DE HORMÔNIOS | 10,9
# nmol/L` + `SEXUAIS`): só CAIXA ALTA, curta, sem número, coluna nem dois-pontos.
_NAME_CONTINUATION = re.compile(r"^[A-ZÀ-Ý][A-ZÀ-Ý ,/()\-]{0,38}$")
_TITLE_WITH_VALUE = re.compile(r"^[A-ZÀ-Ý0-9][^|:a-z]*\s\|\s[<>]?\s*\d")
_SUPPORT_LAB = re.compile(
    r"realizado\s+em\s+laborat[oó]rio\s+de\s+apoio\s+(.+?)(?:\.|$)", re.IGNORECASE
)
_RECEIVED_COLLECTED = re.compile(
    r"recebido\s*/\s*coletado\s+em\s*:?\s*(\d{2}/\d{2}/\d{4})", re.IGNORECASE
)
# Linha que a `strip_boilerplate` nunca remove do rodapé: o layout Softl@b imprime a data de
# cadastro (data de emissão do pedido) como última linha de cada página, e ela alimenta o cabeçalho.
_KEEP_IN_BODY = re.compile(r"^data\s+cadastro\s*:")
_PREVIOUS_START = re.compile(r"^anteriores?\s*:", re.IGNORECASE)
_PREVIOUS_PAIR = re.compile(r"(\d{2}/\d{2}/\d{4})\s*[-–•]\s*([<>≤≥]?\s*\d[\d.,]*)")

# --- layout "ficha" (textos dobrados: minúsculos e sem acentos, salvo o cabeçalho) -------------
# Cabeçalho de colunas: `RESULTADO` em CAIXA ALTA (notas começam por "resultado" minúsculo).
_RESULT_HEADER = re.compile(r"^RESULTADO(?!\w)")
_RECEIVED = re.compile(r"^recebido\s*/\s*coletado\s+em")
_SIGNATURE = re.compile(r"^assinatura\s+digital")
_TRAILER = re.compile(r"^(?:exame\s+liberado|liberado\s+por)")
_HISTORY = re.compile(r"^laudo\s+evolutivo")
_CONTINUATION = re.compile(r"^continuacao\s*\(")
# Identificação do paciente repetida em toda página (alimenta o cabeçalho, nunca um bloco).
_FICHA_IDENTITY = re.compile(r"^(?:cliente|data\s+de\s+nascimento|medico)\s*:")
_FICHA_FOOTER = re.compile(
    r"^(?:crbm|a\s+interpretacao\s+do\s+resultado)|impresso\s+em\s*:.*pagina\s*:"
)
_SPECIMEN_WORDS = re.compile(
    r"^(?:soro|plasma|sangue(?: total| venoso| arterial)?|urina(?: isolada| de 24 horas)?"
    r"|fezes|liquor|saliva|escarro)$"
)
_PREVIOUS_ROW = re.compile(r"^\d{5,}\s*\|?\s*(\d{2}/\d{2}/\d{4})\s*\|?\s*([<>≤≥]?\s*\d[\d.,]*)\s*$")
_PREVIOUS_TABLE_START = re.compile(r"^resultados\s+anteriores", re.IGNORECASE)

MAX_LINES_PER_FALLBACK_BLOCK = 120
HEADER_SCAN_LINES = 20
FOOTER_SCAN_LINES = 10


@dataclass
class Block:
    title: str
    lines: list[str]
    page: int
    specimen: str = ""
    method: str = ""
    collected_at: str = ""  # como impresso: "25/08/2026 07:01:57"
    performing_lab: str = ""
    previous: list[tuple[date, str]] = field(default_factory=list)
    layout: str = ""  # "ficha" ou "softlab" quando segmentado por esses layouts

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


def _digit_key(line: str) -> str:
    return re.sub(r"\d", "#", line.strip())


def strip_boilerplate(pages: list[PageText]) -> tuple[list[list[str]], list[str]]:
    """Remove cabeçalho/rodapé repetidos; devolve (páginas limpas, linhas do cabeçalho).

    Uma linha só é boilerplate se se repete na mesma posição e forma um prefixo
    (ou sufixo) **contíguo** da página. A contiguidade evita apagar linhas de
    corpo como "Material: soro", que repetem em várias páginas mas não no topo.
    """
    keyed = [[_digit_key(line) for line in page.lines] for page in pages]
    n = len(pages)
    header_lines: list[str] = []
    if n < 2:
        return [list(page.lines) for page in pages], header_lines

    threshold = max(2, math.ceil(n * 0.6))
    top_counts: Counter = Counter()
    bottom_counts: Counter = Counter()
    for lines in keyed:
        for i, key in enumerate(lines[:HEADER_SCAN_LINES]):
            top_counts[(i, key)] += 1
        for i, key in enumerate(reversed(lines[-FOOTER_SCAN_LINES:])):
            bottom_counts[(i, key)] += 1

    cleaned: list[list[str]] = []
    for page_index, (page, lines) in enumerate(zip(pages, keyed, strict=True)):
        head = 0
        while head < len(lines) and top_counts[(head, lines[head])] >= threshold:
            head += 1
        # Páginas quase idênticas: o cabeçalho nunca avança sobre âncoras de bloco
        # (nem sobre o título que as precede).
        anchor = next((i for i in range(head) if _ANCHOR.match(fold(page.lines[i]))), None)
        if anchor is not None:
            head = max(anchor - 1, 0)
        tail = 0
        while (
            tail < len(lines) - head
            and bottom_counts[(tail, lines[len(lines) - 1 - tail])] >= threshold
            and not _END.search(fold(page.lines[len(lines) - 1 - tail]))
            and not _KEEP_IN_BODY.match(fold(page.lines[len(lines) - 1 - tail]))
        ):
            tail += 1
        if page_index == 0:
            header_lines = page.lines[:head]
        cleaned.append(page.lines[head : len(page.lines) - tail])
    return cleaned, header_lines


def is_name_continuation(line: str) -> bool:
    return _NAME_CONTINUATION.match(line.strip()) is not None


def _meta(block: Block) -> None:
    for line in block.lines:
        if not block.specimen and (m := _SPECIMEN.match(line)):
            # `Material: Sangue | Metodo: Automacao...`: o material é só a primeira coluna.
            specimen, _, rest = m.group(1).partition(" | ")
            block.specimen = specimen.strip(" |")
            if not block.method and (inline := _METHOD.match(rest.strip())):
                block.method = inline.group(1).strip(" |")
        elif not block.method and (m := _METHOD.match(line)):
            block.method = m.group(1).strip(" |")
        elif not block.collected_at and (m := _COLLECTED.search(line)):
            block.collected_at = " ".join(p for p in (m.group(1), m.group(2)) if p)
        elif not block.collected_at and (m := _RECEIVED_COLLECTED.search(line)):
            block.collected_at = m.group(1)
        if not block.performing_lab and (m := _SUPPORT_LAB.search(line)):
            block.performing_lab = m.group(1).strip()


def _parse_previous_table(lines: list[str]) -> list[tuple[date, str]]:
    """Tabela 'RESULTADOS ANTERIORES: FICHA | DATA | RESULTADO' -> [(data, valor impresso)]."""
    pairs: list[tuple[date, str]] = []
    collecting = False
    for line in lines:
        text = line.strip()
        if _PREVIOUS_TABLE_START.match(text):
            collecting = True
        elif collecting and (m := _PREVIOUS_ROW.match(text)):
            day, month, year = (int(p) for p in m.group(1).split("/"))
            try:
                pairs.append((date(year, month, day), m.group(2).replace(" ", "")))
            except ValueError:
                continue
    return pairs


def parse_previous(lines: list[str]) -> list[tuple[date, str]]:
    """Histórico impresso no próprio exame -> [(data, valor impresso)].

    Duas formas: linhas 'Anteriores: dd/mm/aaaa - valor | ...' e a tabelinha
    'RESULTADOS ANTERIORES' (ficha | data | resultado).
    """
    return [*_parse_inline_previous(lines), *_parse_previous_table(lines)]


def _parse_inline_previous(lines: list[str]) -> list[tuple[date, str]]:
    pairs: list[tuple[date, str]] = []
    collecting = False
    for line in lines:
        if _PREVIOUS_START.match(line.strip()):
            collecting = True
        elif collecting and not _PREVIOUS_PAIR.search(line):
            collecting = False
        if not collecting:
            continue
        for raw_date, raw_value in _PREVIOUS_PAIR.findall(line):
            day, month, year = (int(p) for p in raw_date.split("/"))
            try:
                pairs.append((date(year, month, day), raw_value.replace(" ", "")))
            except ValueError:
                continue
    return pairs


def split_blocks(page_lines: list[list[str]]) -> tuple[list[str], list[Block]]:
    """Retorna (linhas antes do primeiro bloco, blocos)."""
    flat: list[tuple[int, str]] = [
        (page_number, line)
        for page_number, lines in enumerate(page_lines, start=1)
        for line in lines
    ]
    anchors = [i for i, (_, line) in enumerate(flat) if _ANCHOR.match(fold(line))]

    if _is_softlab([line for _, line in flat]):
        return _split_softlab_blocks(page_lines)

    if not anchors:
        ficha = _split_ficha_blocks(page_lines)
        if ficha is not None:
            return ficha
        return [], _fallback_blocks(page_lines)

    starts = []
    for a in anchors:
        title = a - 1
        if title >= 1 and _METHOD.match(flat[title][1]):
            title -= 1  # `Método:` antes de `Material:`: o título vem antes do método
        if title >= 1 and is_name_continuation(flat[title][1]):
            if _TITLE_WITH_VALUE.match(flat[title - 1][1].strip()):
                title -= 1  # o nome continua na linha seguinte ao resultado
        if title < 0 or _END.search(fold(flat[title][1])) or (starts and title <= starts[-1]):
            starts.append(a)  # sem título: o bloco começa na âncora
        else:
            starts.append(title)

    preface = [line for _, line in flat[: starts[0]]]
    blocks: list[Block] = []
    for index, start in enumerate(starts):
        stop = starts[index + 1] if index + 1 < len(starts) else len(flat)
        chunk = flat[start:stop]
        end = next((i for i, (_, line) in enumerate(chunk) if _END.search(fold(line))), None)
        body = chunk if end is None else chunk[:end]
        lines = [line for _, line in body]
        title = lines[0] if lines and not _ANCHOR.match(fold(lines[0])) else ""
        block = Block(title=title, lines=lines, page=chunk[0][0], previous=parse_previous(lines))
        _meta(block)
        if not block.collected_at and end is not None:
            # `Liberado por: ... Data coleta: | 09/05/2011 07:10`: a coleta vem na linha de fim.
            if m := _COLLECTED.search(chunk[end][1]):
                block.collected_at = " ".join(p for p in (m.group(1), m.group(2)) if p)
        blocks.append(block)
    return preface, blocks


def split_title(title: str) -> tuple[str, str]:
    """`COLESTEROL TOTAL, soro` -> (`COLESTEROL TOTAL`, `soro`); sem material conhecido, o todo."""
    name, sep, specimen = title.rpartition(",")
    if sep and name.strip() and _SPECIMEN_WORDS.match(fold(specimen).strip()):
        return name.strip(), specimen.strip()
    return title.strip(), ""


def _ficha_items(page_lines: list[list[str]]) -> list[tuple[int, str]]:
    """Linhas do layout ficha sem o que não é exame: laudo evolutivo (histórico de outras
    fichas, que nunca vira resultado atual), cabeçalho/rodapé que sobrou e marcadores de
    continuação de página."""
    items: list[tuple[int, str]] = []
    for number, lines in enumerate(page_lines, start=1):
        for line in lines:
            if _HISTORY.match(fold(line).strip()):
                break  # o resto da página é histórico
            items.append((number, line))
    return items


def _split_ficha_blocks(page_lines: list[list[str]]) -> tuple[list[str], list[Block]] | None:
    """Blocos do layout ficha, ou `None` se o laudo não tem esse layout."""
    items = _ficha_items(page_lines)
    folded = [fold(line).strip() for _, line in items]
    has_header = any(_RESULT_HEADER.match(line.strip()) for _, line in items)
    has_end = any(_RECEIVED.match(f) or _SIGNATURE.match(f) for f in folded)
    if not (has_header and has_end):
        return None

    preface: list[str] = []
    blocks: list[Block] = []
    current: list[tuple[int, str]] = []
    in_trailer = False

    def close() -> None:
        nonlocal current, in_trailer
        in_trailer = False
        if current:
            blocks.append(_ficha_block(current))
        current = []

    for (number, line), text in zip(items, folded, strict=True):
        if _FICHA_IDENTITY.match(text):
            if not blocks and not current:
                preface.append(line)  # só o cabeçalho do topo alimenta a leitura do laudo
            continue
        if _FICHA_FOOTER.search(text) or _CONTINUATION.match(text):
            continue
        if _SIGNATURE.match(text):
            close()
            continue
        if in_trailer:
            if _TRAILER.match(text):
                continue
            close()  # sem assinatura: a linha abre o exame seguinte
        current.append((number, line))
        if _RECEIVED.match(text):
            in_trailer = True
    close()
    return preface, blocks


def _ficha_block(items: list[tuple[int, str]]) -> Block:
    lines = [line for _, line in items]
    block = Block(
        title=lines[0],
        lines=lines,
        page=items[0][0],
        previous=parse_previous(lines),
        layout="ficha",
    )
    _meta(block)
    block.specimen = block.specimen or split_title(block.title)[1]
    return block


# --- layout "Softl@b" --------------------------------------------------------------------------
# Textos dobrados (minúsculos e sem acentos). A assinatura precisa de algo que só esse layout
# imprime: o nome do sistema, `DATA LIBERAÇÃO:` ou a linha pontilhada longa entre exames. O
# layout original (`Material:` + `Liberado por`) nunca é confundido com este.
_SOFTLAB_SIGNATURE = re.compile(r"softl@b|data\s+(?:da\s+)?liberacao\s*:|^\.{100,}$")
_RELEASED_BY = re.compile(r"^liberad[oa]\s+por")
_SOFTLAB_IDENTITY = re.compile(r"^(?:pedido\s*:|medico\s*:|data\s+cadastro\s*:)")
_SOFTLAB_NOISE = re.compile(
    r"^(?:convenio\s*:|softl@b|pag\.?\s*:|lab\.\s|direcao\s*:|n[º°o]\s*licen"
    r"|documento\s+assinado)|crm-pj|que\s+institui\s+a\s+infraestrutura"
)
_BORDER = re.compile(r"^[+*|\-=\s]{5,}$")
_DOTS = re.compile(r"^\.{5,}$")
MIN_SOFTLAB_SEPARATOR = 100  # a pontilhada que separa exames; as curtas são só enfeite de tabela
_SEPARATOR = None  # marca, na lista de linhas da página, uma pontilhada longa


def _is_softlab(lines: list[str]) -> bool:
    folded = [fold(line).strip() for line in lines]
    return any(_SOFTLAB_SIGNATURE.search(f) for f in folded) and not any(
        _RELEASED_BY.match(f) for f in folded
    )


def _split_softlab_blocks(page_lines: list[list[str]]) -> tuple[list[str], list[Block]]:
    """Blocos do layout Softl@b e as linhas de identificação da primeira página.

    Cabeçalho (`Pedido:`, `Médico:`, `Convênio:`, `Pag.:`) e rodapé de assinatura nunca entram
    num bloco; bordas de tabela e pontilhadas curtas também não.
    """
    preface: list[str] = []
    blocks: list[Block] = []
    for number, lines in enumerate(page_lines, start=1):
        items: list[str | None] = []
        for line in lines:
            folded = fold(line).strip()
            if _SOFTLAB_IDENTITY.match(folded):
                if number == 1:
                    preface.append(line)
                continue
            if _SOFTLAB_NOISE.search(folded) or _BORDER.match(line.strip()):
                continue
            if _DOTS.match(line.strip()):
                if len(line.strip()) >= MIN_SOFTLAB_SEPARATOR:
                    items.append(_SEPARATOR)
                continue
            items.append(line)
        blocks.extend(_softlab_page_blocks(number, items))
    return preface, blocks


def _softlab_page_blocks(number: int, items: list[str | None]) -> list[Block]:
    """Divide a página em blocos de exame.

    Um exame começa no primeiro texto da página, depois de uma pontilhada longa ou de
    `DATA DA COLETA:`/`DATA LIBERAÇÃO:`, ou numa linha de título com unidade (`NOME | 4.5 g/dl`).
    Painéis (título só com o nome) absorvem as linhas de resultado até serem fechados. Dentro da
    seção de referência só uma linha em colunas e com unidade abre um novo exame, para as
    faixas impressas sem coluna (`ALTO ... MAIOR OU IGUAL A 60 mg/dl`) ficarem na referência.
    """
    groups: list[list[str]] = []
    current: list[str] | None = None
    kind = "single"
    title_name = ""
    in_reference = closed = False

    for index, line in enumerate(items):
        if line is _SEPARATOR:
            closed = True
            in_reference = False
            continue
        folded = fold(line).strip()
        row = parse_row(line)
        following = next((n for n in items[index + 1 :] if n is not _SEPARATOR), None)
        start = current is None
        if not start and row is not None and row.strong and row.name[0].isalpha():
            if kind == "panel":
                start = closed
            else:
                piped_in_reference = in_reference and " | " in line and not row.has_comparator
                start = (piped_in_reference or not in_reference) and not is_percent_variant(
                    row.name, title_name
                )
        elif not start and is_title_only(line) and following is not None:
            start = starts_method_or_material(fold(following).strip())
        if start:
            current = []
            groups.append(current)
            in_reference = closed = False
            if row is not None:
                kind, title_name = "single", row.name
            elif is_title_only(line):
                kind, title_name = "panel", line.strip()
            else:
                kind, title_name = "single", ""
        assert current is not None
        current.append(line)

        if reference_header(line) is not None:
            in_reference = True
        elif in_reference and (is_meta(folded) or is_footnote(line)):
            in_reference = False
        if is_closer(folded):
            closed = True

    blocks = []
    for lines in groups:
        block = Block(title=lines[0], lines=lines, page=number, layout="softlab")
        _meta(block)
        blocks.append(block)
    return blocks


def _fallback_blocks(page_lines: list[list[str]]) -> list[Block]:
    blocks = []
    for page_number, lines in enumerate(page_lines, start=1):
        for offset in range(0, len(lines), MAX_LINES_PER_FALLBACK_BLOCK):
            chunk = lines[offset : offset + MAX_LINES_PER_FALLBACK_BLOCK]
            if chunk:
                block = Block(
                    title="", lines=chunk, page=page_number, previous=parse_previous(chunk)
                )
                _meta(block)
                blocks.append(block)
    return blocks


def find_source_line(block: Block, name: str, value_text: str) -> str:
    """Linha do bloco de onde saiu o resultado (derivada em código, não pedida ao modelo).

    Prefere a linha que contém o valor (comparação sem espaços) **e** a primeira palavra
    do nome; senão a primeira que contém o valor. Sem valor transcrito, a linha do nome.
    """
    word = next(iter(normalize_name(name).split()), "")
    needle = compact(value_text)
    if not needle:
        return next((line for line in block.lines if word and word in fold(line)), "")
    with_value = [line for line in block.lines if needle in compact(line)]
    if not with_value:
        return ""
    return next((line for line in with_value if word and word in fold(line)), with_value[0])
