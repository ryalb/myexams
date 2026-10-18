"""Prompts do LLM e o único JSON Schema que sobrou (resolução de exame).

O modelo só transcreve: valores, unidades e referências saem exatamente como
impressos, em texto. Conversão, interpretação de faixa e sinalização são feitas
em código. Cabeçalho e resultados saem em texto compacto, sem gramática JSON (ver
`compact.py`). Prompts em português; `PROMPT_VERSION` entra na ExtractionRun.
"""

PROMPT_VERSION = "2026-10-3"

MATCH_SCHEMA = {
    "type": "object",
    "properties": {"slug": {"type": "string"}},
    "required": ["slug"],
}

HEADER_PROMPT = """Você lê o cabeçalho de um laudo de exames laboratoriais brasileiro.
Responda EXATAMENTE 7 linhas, nesta ordem, no formato "chave: valor", copiando o texto
como impresso. Se um campo não aparece, deixe o valor vazio depois dos dois-pontos.
Sem explicações, sem listas, sem markdown.

lab:
accession:
patient:
birth:
physician:
issued:
collected:

Significado: lab = nome do laboratório que emite o laudo (não o de apoio); accession = número
do pedido/atendimento ("O.S"); patient = nome do paciente; birth = data de nascimento;
physician = médico solicitante; issued = data e hora de emissão (ex.: "31/08/2026 -
13:57:40"); collected = data e hora da coleta.

Regras: não invente nada; ignore CPF, RG e demais documentos (já foram removidos).

Texto do cabeçalho:
{text}
"""

# Saída em texto simples, uma linha por grandeza: gerar JSON restrito custava muito mais
# tokens e, a ~4,5 tok/s, estourava o tempo. A referência impressa FORA da linha do
# resultado (a maioria dos exames) é copiada pelo código, não pelo modelo.
LINES_PROMPT = """Você extrai resultados de um trecho de laudo de exames laboratoriais brasileiro.
As linhas do trecho são numeradas ([1], [2]...). Colunas aparecem separadas por " | ".
Uma célula pode trazer dois valores (percentual e absoluto).

Responda SOMENTE com linhas neste formato, uma por grandeza medida:
n|nome|valor|unidade|calc|referência

- n: o número da linha do trecho de onde saiu o valor (só o número, sem colchetes).
- nome: o nome do exame/grandeza como impresso.
- valor: SOMENTE o valor medido, exatamente como impresso ("4,85", "<0,5", "Negativo").
  Se o resultado estiver ausente ou ilegível, deixe vazio: NUNCA invente um número.
- unidade: a unidade impressa junto ao resultado; se não houver, vazio.
- calc: 1 se o laudo diz que o valor é calculado/estimado, senão 0.
- referência: a faixa de referência impressa NA MESMA LINHA do resultado (ex.: "4,50 a 6,50
  milhões/uL"). Se a referência estiver impressa em outras linhas, deixe vazio.

Regras obrigatórias:
- TRANSCREVA, nunca calcule, converta, corrija nem deduza. Mantenha vírgula decimal e
  pontos de milhar.
- Uma linha de saída por grandeza. Se uma linha traz duas colunas de resultado (percentual
  e valor absoluto), escreva DUAS linhas com o mesmo n: a segunda com o nome seguido de
  " (absoluto)" e com o valor, a unidade e a referência do valor absoluto.
- Não crie linhas para títulos de seção (ex.: ERITROGRAMA, LEUCOGRAMA), observações, notas,
  rodapés nem "Anteriores".
- Nada além dessas linhas: sem explicação, sem cabeçalho, sem JSON, sem markdown.

Exemplo (entrada):
[1] Hemácias | 4,85 milhões/uL | 4,50 a 6,50 milhões/uL
[2] Hemoglobina | g/dL | 13,50 a 18,00 g/dL
[3] Neutrófilos | 60,0 % 2.400 /uL | 40,0 a 75,0 | 2.000 a 7.000
Exemplo (saída):
1|Hemácias|4,85|milhões/uL|0|4,50 a 6,50 milhões/uL
2|Hemoglobina||g/dL|0|13,50 a 18,00 g/dL
3|Neutrófilos|60,0|%|0|40,0 a 75,0
3|Neutrófilos (absoluto)|2.400|/uL|0|2.000 a 7.000

Trecho (página {page}):
{text}
"""

MATCH_PROMPT = """Qual exame do catálogo corresponde ao nome impresso no laudo?
Nome impresso: "{name}"
Unidade: "{unit}"
Catálogo (slug: nome):
{candidates}

Responda um JSON {{"slug": "<slug>"}} com um slug do catálogo, ou {{"slug": ""}} se
nenhum corresponder com segurança. Não invente slugs.
"""

VISION_PROMPT = """Transcreva TODO o texto desta página de laudo de exames, linha a linha,
na ordem de leitura. Em tabelas, separe as colunas de cada linha com " | ".
Copie números, unidades e símbolos exatamente como impressos. Não resuma, não
interprete e não adicione nada. Responda apenas com a transcrição."""
