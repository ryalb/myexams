"""Páginas sintéticas no formato "Softl@b" (cabeçalho `Pedido:`, exames separados por linha
pontilhada, `DATA DA COLETA:` ou só pelo título seguinte).

Dados inventados (nunca laudos reais): só imitam a forma impressa. As linhas já estão no formato
que `linearize` produz (colunas separadas por ` | `).
"""

from apps.exams.extraction.linearize import PageText

PATIENT = "JOAO TESTE DA SILVA"
ORDER = "999000-01"
ISSUED = "12/03/2024"
SEPARATOR = "." * 120
PHYSICIAN = "FULANA DE TAL"

FOOTER = [
    "LAB. EXEMPLO CRM-PJ 00",
    "DIREÇÃO: Dr. Fulano Exemplo CRM 000",
    "Nº Licença 000000/0",
    "Documento assinado digitalmente Nº 9990000000000, Versão [ID], conforme MP n°2.200-2/2001 de",
    "24/08/2001, que institui a Infraestrutura de Chaves Públicas Brasileira - ICP-Brasil.",
]


def header_lines(number: int, total: int, *, cadastro_on_top: bool = True) -> list[str]:
    doctor = f"Médico: {PHYSICIAN}"
    if cadastro_on_top:
        doctor += f" | Data cadastro: {ISSUED}"
    return [
        f"Pedido: {ORDER} {PATIENT}",
        doctor,
        "Convênio: PARTICULAR EXEMPLO | CENTRO",
        "Softl@b",
        f"Pag.: {number} de {total}",
    ]


def page(number: int, total: int, body: list[str], *, cadastro_on_top: bool = True) -> PageText:
    lines = [*header_lines(number, total, cadastro_on_top=cadastro_on_top), *body, *FOOTER]
    if not cadastro_on_top:
        lines.append(f"Data cadastro: {ISSUED}")
    return PageText(number, lines, "text")


def pages(*bodies: list[str], cadastro_on_top: bool = True) -> list[PageText]:
    return [
        page(number, len(bodies), body, cadastro_on_top=cadastro_on_top)
        for number, body in enumerate(bodies, start=1)
    ]


# --- exames de resultado único (formato com pontilhada entre exames) --------------------------

ALBUMIN = [
    "ALBUMINA | 3.9 g/dl",
    "MÉTODO:COLORIMÉTRICO - EXEMPLO",
    "MATERIAL:SORO",
    "VALORES DE REFERÊNCIA:3.2 A 4.8 g/dl",
    "DATA DA COLETA:12/03/2024",
    "DATA LIBERAÇÃO:12/03/2024",
    "ATENÇÃO PARA NOVOS VALORES DE REFERÊNCIA",
    SEPARATOR,
]
ESTRADIOL = [
    "ESTRADIOL | 23.4 pg/ml.",
    "Resultados Anteriores:19.6[02/08/2023];",
    "MÉTODO: ELETROQUIMIOLUMINESCÊNCIA",
    "MATERIAL: SORO",
    "VALORES DE REFERÊNCIA: HOMENS: PRÉ-PUBERES: 2.0 A 8.0 pg/ml",
    "ADULTOS | :10.0 A 35.0 pg/ml",
    "MULHERES: PRÉ-PUBERES: 4.0 A 12.0 pg/ml",
    "FASE FOLICULAR: 5.4 A 214.0 pg/ml",
    "PÓS-MENOPAUSA: | 3.0 A 30.0 pg/ml",
    "OBS: NOVO MÉTODO E NOVOS VALORES DE REFERÊNCIA A PARTIR DE 10/10/2010",
    "DATA DA COLETA:12/03/2024 07:32:00",
    SEPARATOR,
]
LH = [
    "LH - ESPECÍFICO | 6.27 mUI/ml",
    "Resultados Anteriores:3.18[02/08/2023];5.43[11/11/2022];",
    "MÉTODO: ELETROQUIMIOLUMINASCÊNCIA",
    "MATERIAL:SORO",
    "VALORES DE REFERÊNCIA: PRÉ-PUBERAL:",
    "AMBOS OS SEXOS: INFERIOR À 0,150 mUI/ml",
    "HOMENS: 1,0 A 8,4 mUI/ml",
    "MULHERES: FASE FOLICULAR: 2.58 A 12.10 mUI/ml",
    "FASE LÚTEA | : 0.830 A 15.50 mUI/ml",
    "DATA DA COLETA:12/03/2024 07:32:00",
    SEPARATOR,
]
CREATININE = [
    "CREATININA | 0.8 mg/dl",
    "MÉTODO: COLORIMÉTRICO - EXEMPLO",
    "MATERIAL:SORO",
    "VALORES DE REFERÊNCIA: HOMENS : 0.7 A 1.3 mg/dl",
    "MULHERES: 0.5 A 1.1 mg/dl",
    "CRIANÇAS: 0.0 A 0.7 mg/dl",
    "DATA DA COLETA:12/03/2024 | DATA LIBERAÇÃO:12/03/2024",
]
TESTOSTERONE = [
    "TESTOSTERONA | 512.0 | ng/100ml.",
    "MÉTODO:ELETROQUIMIOLUMINESCÊNCIA",
    "MATERIAL:SORO",
    "VALORES DE REFERÊNCIA: MULHER ADULTA:6,0 A 82,0 ng/100ml.",
    "HOMEM ADULTO:250,0 A 800,0 ng/100ml",
    "RAPAZES | :< 1 ANO 12,0 A 21,0 ng/100ml.",
    "1 A 6 ANOS: 3,0 A 32,0 ng/100ml.",
    SEPARATOR,
]
TSH = [
    "TSH ULTRA SENSÍVEL | 2.31 uUI/ml.",
    "Resultados Anteriores:1.87[11/11/2022];",
    "MÉTODO: ELETROQUIMIOLUMINESCÊNCIA",
    "MATERIAL:SORO",
    "VALORES DE REFERÊNCIA: 0.4 A 5.50 uUI/ml",
    "OBS: NOVO MÉTODO E NOVOS VALORES DE REFERÊNCIA A PARTIR DE 10/10/2010",
    SEPARATOR,
]
T4 = [
    "T4 LIVRE | 1.33 ng/100ml.",
    "MÉTODO: ELETROQUIMIOLUMINESCÊNCIA",
    "MATERIAL:SORO",
    "VALORES DE REFERÊCNIA: 0,77 A 2.19 ng/100ml.COM MÉDIA DE 1.23 ng/100ml",
    "DATA DA COLETA:12/03/2024",
    SEPARATOR,
]
VITAMIN_D = [
    "25 - HIDROXIVITAMINA D | 36.2 ng/ml",
    "MÉTODO:QUIMIOLUMINESCENCIA",
    "MATERIAL:SORO",
    "VALORES DE REFERÊNCIA*: DEFICIÊNCIA : MENOR QUE 10.0 ng/ml",
    "INSUFICIÊNCIA: de 10,0 a 20,0 ng/ml",
    "SUFICIÊNCIA* : MAIOR QUE 20.0 ng/ml",
    "DATA DA COLETA:12/03/2024",
    "* OS VALORES DE 25(HO)D CONSIDERADOS SUFICIENTES PARA O METABOLISMO ÓSSEO",
    "ESSES VALORES SITUAM-SE ENTRE 30.0 E 90.0 ng/ml",
    "NOTA : OS NÍVEIS DE 25(OH)VITAMINA D APRESENTAM IMPORTANTES VARIAÇÕES.",
]
ESR = [
    "ERITROSSEDIMENTAÇÃO NA 1ª HORA | 6 mm.",
    "RESULTADOS ANTERIORES :4[10/05/2023];8[22/02/2023];",
    "VALORES DE REFERÊNCIA: | HOMENS ABAIXO DE 50 ANOS: < 15mm.",
    "50 A 85 ANOS: | < 20mm.",
    "DATA DA COLETA:12/03/2024 08:38:00DATA LIBERAÇÃO:12/03/2024",
]
GLUCOSE = [
    "GLICOSE (GLICEMIA JEJUM) | 89.0 mg/dl",
    "MÉTODO:CINÉTICO ENZIMÁTICO - EXEMPLO",
    "VALORES DE REFERÊNCIA: 60 - 99 mg/dl",
    "ATENÇÃO PARA NOVOS VALORES DE REFERÊNCIA",
    SEPARATOR,
]
POST_PRANDIAL = [
    "GLICOSE PÓS PRANDIAL | 118.0 mg/dL",
    "VALOR DE REFERÊNCIA: ATÉ 140,0 mg/dL",
    SEPARATOR,
]
TOTAL_PROTEIN = [
    "PROTEÍNAS TOTAIS | 7.2g/dl",
    "VALORES DE REFERÊNCIA : 5.7 A 8.2 mg/dl",
    "DATA DA COLETA:12/03/2024",
]
ESTRONE = [
    "ESTRONA | 88.5pg/ml",
    "VALORES DE REFERÊNCIA:",
    "- HOMENS : | 9.4 A 80.0",
    "- MULHERES:",
    "EM IDADE FÉRTIL : 11.0 A 340.0 pg/ml",
    "DATA DA COLETA:12/03/2024",
]
T3 = [
    "T3 | 121.3 ng/100ml.",
    "VALORES DE REFERÊNCIA: | CRIANÇAS:",
    "ATÉ 5 ANOS: | 105,0 A 169,0 ng/100ml",
    "ADULTOS:",
    "20 A 50 ANOS: | 70,0 A 200,0 ng/100ml",
    "DATA DA COLETA:12/03/2024",
]
SHBG = [
    "SHBG - GLOBULINA LIGADORA DE HORMÔNIOS SEXUAIS | 13.7 nmol/L",
    "MÉTODO: ELETROQUIMIOLUMINESCÊNCIA",
    "VALORES DE REFERÊNCIA: HOMENS:14.5 A 48.4 nmol/L",
    "MULHERES(NÃO GRAVIDAS):26.1 A 110.0 nmol/L",
    "[@]",
    "DATA DA COLETA:12/03/2024",
]
IGFBP = [
    "IGFBP - 3 | PROTEINA LIGADORA-3 DO IGF | 4,8 mcg/mL",
    "VALORES DE REFERÊNCIA",
    "| FAIXA ETARIA | | mcg/mL | |",
    "| 1 A 7 DIAS | MENOR QUE 0,8 |",
    "| 8 A 15 DIAS | | 0,5 A 1,4 | |",
    "DATA DA COLETA:12/03/2024",
    "DATA LIBERAÇÃO:14/03/2024",
]
CHROMIUM = [
    "CROMO | 0,8 µg/L",
    "(METODOLOGIA IN HOUSE)",
    "VALOR DE REFERENCIA: ATÉ 5,0 μg/L",
    "DATA DA COLETA:12/03/2024",
]
COVID = [
    "TESTE RÁPIDO PARA COVID-19",
    "MÉTODO:CROMATOGRÁFICO -MedTeste",
    "REGISTRO ANVISA : MS 00000000000",
    "VALORES DE REFERÊNCIA: | NEGATIVO",
    "ANTICORPOS IgG | NEGATIVO",
    "ANTICORPOS IgM | POSITIVO",
    "UM RESULTADO NEGATIVO POR MÉTODOS SOROLÓGICOS NÃO EXCLUI A POSSIBILIDADE DE",
    "OBS: Para confirmar infecção recente : realizar o ensaio 20 dias após o início",
    "DATA DA COLETA:12/03/2024",
    SEPARATOR,
]
VDRL = ["SOROLOGIA PARA LUES - VDRL | NEGATIVO", SEPARATOR]

# --- painéis ---------------------------------------------------------------------------------

HEMOGRAM = [
    "HEMOGRAMA",
    "MÉTODO: COULTER EXEMPLO - com Revisão de Lâmina.",
    "MATERIAL: SANGUE",
    "SÉRIE VERMELHA | VALORES DE REFERÊNCIA",
    "HOMENS | MULHERES",
    "HEMÁCIAS.....: | 5.02 milhões/ul | 4.7 - 6.1 | 4.2 - 5.4",
    "HEMOGLOBINA..: | 15.8 g/dl | 14.0 - 18.0 | 12.0 - 16.0",
    "VCM..........: | 88.1 u3 | 80.0 - 98.0 | 80.0 - 98.0",
    "SÉRIE BRANCA",
    "LEUCÓCITOS | 7.200 /mm3 | 4.000 - 11.000",
    "BLASTOS | 0 % | 0 / mm3",
    "BASTÕES | 0 % | 0 / mm3 | 0 - 3 | 0 - 330",
    "SEGMENTADOS | 63 % | 4.536 / mm3 | 55 - 65 | 2.310 - 7.150",
    "CONTAGEM DE PLAQUETAS | 238.000 /mm3 | 150.000 - 450.000",
    "MPV | 10.8 fL | 7.0 - 10.0",
    "ASPECTOS CITOMORFOLÓGICOS",
    "NORMOCITOSE E NORMOCROMIA.",
    "DATA DA COLETA:12/03/2024 08:14:00 | DATA LIBERAÇÃO:12/03/2024",
]
PSA = [
    "PSA TOTAL / PSA LIVRE",
    "MÉTODO: ELETROQUIMIOLUMINESCÊNCIA",
    "MATERIAL:SORO",
    "PSA TOTAL | 1.12 ng/mL",
    "Resultados Anteriores:0.94[02/08/2023];",
    "VALORES DE REFERÊNCIA: < 4 ng/ml",
    "PSA LIVRE | 0.21 ng/mL",
    "Resultados Anteriores:0.26[02/08/2023];",
    "RELAÇÃO PSA LIVRE / PSA TOTAL | 18.7 %",
    "DATA DA COLETA:12/03/2024",
    "DATA LIBERAÇÃO:15/03/2024",
    "OBS: A dosagem do PSA livre tem importância quando os valores do PSA",
    "total são superiores a 4.0 ng / ml.",
]
HBA1C = [
    "HEMOGLOBINA GLICADA",
    "MÉTODO:HPLC/CROMATOGRAFIA DE TROCA IÔNICA",
    "MATERIAL: SANGUE",
    "VALORES DE REFERÊNCIA: 4.0 A 6.5 %",
    "% HEMOGLOBINA A1C | 5.4",
    "Resultados Anteriores:5.6[22/03/2023];",
    "GLICEMIA MÉDIA ESTIMADA | 113 | mg/dl",
    "DATA DA COLETA:12/03/2024 | DATA LIBERAÇÃO:12/03/2024",
]
BILIRUBIN = [
    "BILIRRUBINA TOTAL E FRAÇÕES",
    "MÉTODO: COLORIMÉTRICO - EXEMPLO",
    "BILIRRUBINA TOTAL | 0.8 mg/dl.",
    "VALORES DE REFERÊNCIA: 0.30 A 1.20 mg/dl.",
    "BILIRRUBINA DIRETA | 0.2 mg/dl.",
    "VALORES DE REFERÊNCIA: ATÉ 0.30 mg/dl.",
    "DATA DA COLETA:12/03/2024",
]
HDL_AFTER_BOTH_ROWS = [
    "COLESTEROL HDL | 49.5 mg/dL",
    "MÉTODO : CINÉTICO ENZIMÁTICO - EXEMPLO",
    "COLESTEROL HDL(%) | 21.3 %",
    "VALORES DE REFERÊNCIA :",
    "+-------------------+-----------------+-----------------------+",
    "* | ADULTOS | * ADULTOS | * CATEGORIA REFERÊNCIAL *",
    "*-------------------*-----------------*-----------------------*",
    "* | > 40 | * | > 40 | * | DESEJÁVEL | *",
    "+-------------------+-----------------*-----------------------+",
    "- Fonte:Consenso Brasileiro para a Normatização da Determinação Laboratorial do Perfil",
    "DATA DA COLETA:12/03/2024 | DATA LIBERAÇÃO:12/03/2024",
]
HDL_REFERENCE_BETWEEN_ROWS = [
    "COLESTEROL HDL | 51.0 mg/dl",
    "VALORES DE REFERÊNCIA : BAIXO(RISCO ALTO INDESEJADO) <40 mg/dl",
    "ALTO (RISCO BAIXO DESEJADO) MAIOR OU IGUAL A 60 mg/dl",
    "COLESTEROL HDL(%) | 19.8 %",
    "DATA DA COLETA:12/03/2024",
    SEPARATOR,
]
TOTAL_CHOLESTEROL_TABLE = [
    "COLESTEROL TOTAL | 181.0 mg/dL",
    "MÉTODO : CINÉTICO ENZIMÁTICO - EXEMPLO",
    "VALORES DE REFERÊNCIA:",
    "+-------------------+-----------------+-----------------------+",
    "* | ADULTOS | * ADULTOS | * CATEGORIA REFERÊNCIAL *",
    "* | < 190 | * | < 190 | * | DESEJÁVEL | *",
    "* Em adultos o colesterol Total >310 mg/dL há probabilidade de hipercolesterolemia.",
    "DATA DA COLETA:12/03/2024 | DATA LIBERAÇÃO:12/03/2024",
]

# Blocos que as regras NÃO leem (o bloco inteiro vai ao LLM).
BIOAVAILABLE_TESTOSTERONE = [
    "TESTOSTERONA LIVRE CALCULADA | (cFT) | 98.0 picog/ml",
    "VALORES DE REFERÊNCIA: HOMENS: de 17 A 60 anos 37.0 A 246.0 pg/ml",
    "ÍNDICE DOS ANDROGÊNIOS LIVRES(FAI) | 47.0",
    "VALORES DE REFERÊNCIA: HOMENS: 14.8 A 95.0",
    "TESTOSTERONA BIODISPONÍVEL(BaT) | 301.0 | ng/dl | 61.0 %",
    "DA TESTOSTERONA CIRCULANTE",
    "DATA DA COLETA:12/03/2024",
]
HOMA = [
    "INSULINA JEJUM | 12.0 | micro UI/ml",
    "GLICEMIA JEJUM | 5.10 | mmol/L (92 | mg/dl)",
    "INDICE DE HOMA",
    "HOMA BETA | 150.25",
    "DATA DA COLETA:12/03/2024",
    SEPARATOR,
]
BLEEDING_TIME = [
    "COAGULOGRAMA",
    "TEMPO DE SANGRIA | 2 min 10 seg",
    "VALORES DE REFERÊNCIA: 1 A 3 min.",
    "CONTAGEM DE PLAQUETAS | 220.000 | /mm3",
    "VALORES DE REFERÊNCIA: 150.000 A 450.000 /mm3.",
]
