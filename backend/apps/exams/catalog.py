"""Catálogo canônico de exames (pt-BR) semeado por migration.

Cada entrada: slug, nome, categoria, unidade canônica, apelidos (como aparecem nos
laudos), limites de plausibilidade na unidade canônica e conversões de unidade.
Percentual e contagem absoluta são exames distintos (`*_pct` / `*_abs`), e
exames calculados (eRFG, testosterona livre) têm `calculated=True`.
`sync_catalog` é idempotente: roda na migration e pelo comando `sync_catalog`.
"""

from dataclasses import dataclass, field
from decimal import Decimal

from .extraction.text import normalize_name, sorted_tokens_key


@dataclass(frozen=True)
class Entry:
    slug: str
    name: str
    category: str
    unit: str
    aliases: tuple[str, ...] = ()
    minimum: str | None = None
    maximum: str | None = None
    calculated: bool = False
    kind: str = "numeric"
    conversions: tuple[tuple[str, str], ...] = field(default_factory=tuple)  # (de, fator)


def _diff(
    slug: str, name: str, extra: tuple[str, ...] = (), extra_abs: tuple[str, ...] = ()
) -> list[Entry]:
    base = normalize_name(name)
    return [
        Entry(
            f"{slug}_pct",
            f"{name} (%)",
            "hematology",
            "%",
            (name, f"{name} percentual", *extra),
            "0",
            "100",
        ),
        Entry(
            f"{slug}_abs",
            f"{name} (absoluto)",
            "hematology",
            "/µL",
            (f"{name} absoluto", f"{name} absolutos", f"{name} abs", f"{base} (/uL)", *extra_abs),
            "0",
            "100000",
        ),
    ]


CATALOG: list[Entry] = [
    # Hematologia
    Entry(
        "hemacias", "Hemácias", "hematology", "milhões/µL", ("Hemácias", "Eritrócitos"), "1", "9"
    ),
    Entry(
        "hemoglobina",
        "Hemoglobina",
        "hematology",
        "g/dL",
        ("Hemoglobina", "Hb"),
        "3",
        "25",
        conversions=(("g/L", "0.1"),),
    ),
    Entry("hematocrito", "Hematócrito", "hematology", "%", ("Hematócrito", "Ht"), "10", "70"),
    Entry(
        "vcm", "VCM", "hematology", "fL", ("VCM", "V.C.M", "Volume corpuscular médio"), "50", "150"
    ),
    Entry(
        "hcm",
        "HCM",
        "hematology",
        "pg",
        ("HCM", "H.C.M.", "Hemoglobina corpuscular média"),
        "15",
        "50",
    ),
    Entry(
        "chcm",
        "CHCM",
        "hematology",
        "g/dL",
        ("CHCM", "C.H.C.M", "Concentração de hemoglobina corpuscular média"),
        "25",
        "40",
    ),
    Entry("rdw", "RDW", "hematology", "%", ("RDW", "R.D.W.", "Índice de anisocitose"), "8", "30"),
    Entry("hdw", "HDW", "hematology", "g/dL", ("HDW", "H.D.W."), "1", "6"),
    Entry(
        "leucocitos",
        "Leucócitos",
        "hematology",
        "/µL",
        ("Leucócitos", "Leucócitos totais", "Glóbulos brancos"),
        "500",
        "100000",
        conversions=(("mil/µL", "1000"),),
    ),
    *_diff("neutrofilos", "Neutrófilos"),
    *_diff("bastoes", "Bastões", ("Bastonetes",)),
    *_diff("segmentados", "Segmentados"),
    *_diff("eosinofilos", "Eosinófilos"),
    *_diff("basofilos", "Basófilos"),
    # "Típicos" é a contagem principal de linfócitos no diferencial do laboratório Softl@b; os
    # "atípicos" são outro exame (sem este apelido, o fuzzy poderia trocar um pelo outro).
    *_diff(
        "linfocitos",
        "Linfócitos",
        ("Linfócitos típicos",),
        ("Linfócitos típicos (absoluto)",),
    ),
    *_diff("linfocitos_atipicos", "Linfócitos atípicos"),
    *_diff("blastos", "Blastos"),
    *_diff("promielocitos", "Promielócitos"),
    *_diff("mielocitos", "Mielócitos"),
    *_diff("metamielocitos", "Metamielócitos"),
    *_diff("monocitos", "Monócitos"),
    Entry(
        "plaquetas",
        "Plaquetas",
        "hematology",
        "/µL",
        ("Plaquetas", "Contagem de plaquetas", "Total de plaquetas"),
        "5000",
        "2000000",
        conversions=(("mil/µL", "1000"),),
    ),
    Entry(
        "vpm",
        "Volume plaquetário médio",
        "hematology",
        "fL",
        ("Volume plaquetário M.P.V.", "VPM", "MPV", "Volume plaquetário médio"),
        "4",
        "20",
    ),
    Entry(
        "pdw",
        "PDW",
        "hematology",
        "%",
        ("Ind. anisoplaquetose P.D.W.", "PDW", "Índice de anisoplaquetose"),
        "5",
        "90",
    ),
    # Glicemia
    Entry(
        "glicose",
        "Glicose",
        "glycemia",
        "mg/dL",
        ("Glicose", "Glicemia", "Glicemia de jejum", "Glicose em jejum"),
        "20",
        "800",
        conversions=(("mmol/L", "18.016"),),
    ),
    Entry(
        "hba1c",
        "Hemoglobina glicada (HbA1c)",
        "glycemia",
        "%",
        (
            "Hemoglobina glicada (A1c)",
            "Hemoglobina glicada",
            "HbA1c",
            "A1c",
            "Hemoglobina A1C",
            "% Hemoglobina A1C",
        ),
        "3",
        "20",
    ),
    Entry(
        "glicose_media_estimada",
        "Glicose média estimada",
        "glycemia",
        "mg/dL",
        ("Glicose média estimada", "GME"),
        "40",
        "600",
        calculated=True,
    ),
    Entry("insulina", "Insulina", "glycemia", "µUI/mL", ("Insulina", "Insulina basal"), "0", "300"),
    # Lipidograma
    Entry(
        "colesterol_total",
        "Colesterol total",
        "lipids",
        "mg/dL",
        ("Colesterol total", "Colesterol"),
        "50",
        "700",
        conversions=(("mmol/L", "38.67"),),
    ),
    Entry(
        "colesterol_hdl",
        "Colesterol HDL",
        "lipids",
        "mg/dL",
        ("HDL - Colesterol", "Colesterol HDL", "HDL"),
        "5",
        "150",
        conversions=(("mmol/L", "38.67"),),
    ),
    Entry(
        "colesterol_ldl",
        "Colesterol LDL",
        "lipids",
        "mg/dL",
        ("LDL - Colesterol", "Colesterol LDL", "LDL"),
        "10",
        "500",
        conversions=(("mmol/L", "38.67"),),
    ),
    Entry(
        "colesterol_vldl",
        "Colesterol VLDL",
        "lipids",
        "mg/dL",
        ("VLDL - Colesterol", "Colesterol VLDL", "VLDL"),
        "1",
        "200",
        calculated=True,
    ),
    Entry(
        "colesterol_hdl_pct",
        "Colesterol HDL (%)",
        "lipids",
        "%",
        ("Colesterol HDL(%)", "HDL (%)"),
        "1",
        "100",
        calculated=True,
    ),
    Entry(
        "colesterol_nao_hdl",
        "Colesterol não-HDL",
        "lipids",
        "mg/dL",
        ("Colesterol não-HDL", "Não-HDL", "Colesterol não HDL"),
        "10",
        "600",
        calculated=True,
    ),
    Entry(
        "triglicerides",
        "Triglicérides",
        "lipids",
        "mg/dL",
        ("Triglicérides", "Triglicerídeos"),
        "10",
        "3000",
        conversions=(("mmol/L", "88.57"),),
    ),
    # Função renal
    Entry("ureia", "Ureia", "kidney", "mg/dL", ("Uréia", "Ureia"), "3", "400"),
    Entry(
        "creatinina",
        "Creatinina",
        "kidney",
        "mg/dL",
        ("Creatinina",),
        "0.1",
        "20",
        conversions=(("µmol/L", "0.011312"),),
    ),
    Entry(
        "erfg",
        "eRFG (CKD-EPI)",
        "kidney",
        "mL/min/1,73 m²",
        ("eRFG", "TFG", "Taxa de filtração glomerular estimada", "eGFR"),
        "1",
        "200",
        calculated=True,
    ),
    Entry("acido_urico", "Ácido úrico", "kidney", "mg/dL", ("Ácido úrico",), "1", "20"),
    # Eletrólitos
    # Íons monovalentes: 1 mEq/L = 1 mmol/L.
    Entry(
        "sodio",
        "Sódio",
        "electrolytes",
        "mmol/L",
        ("Sódio", "Na"),
        "100",
        "180",
        conversions=(("mEq/L", "1"),),
    ),
    Entry(
        "potassio",
        "Potássio",
        "electrolytes",
        "mmol/L",
        ("Potássio", "K"),
        "1.5",
        "9",
        conversions=(("mEq/L", "1"),),
    ),
    Entry("magnesio", "Magnésio", "electrolytes", "mg/dL", ("Magnésio", "Mg"), "0.5", "6"),
    Entry(
        "calcio",
        "Cálcio",
        "electrolytes",
        "mg/dL",
        ("Cálcio", "Cálcio total", "Cálcio sérico"),
        "4",
        "18",
    ),
    Entry("fosforo", "Fósforo", "electrolytes", "mg/dL", ("Fósforo",), "1", "12"),
    Entry(
        "cloro",
        "Cloro",
        "electrolytes",
        "mmol/L",
        ("Cloro", "Cloreto"),
        "70",
        "130",
        conversions=(("mEq/L", "1"),),
    ),
    # Função hepática
    Entry(
        "tgo",
        "TGO (AST)",
        "liver",
        "U/L",
        (
            "Transaminase glutâmico oxalacética (TGO/AST)",
            "TGO/AST",
            "TGO",
            "AST",
            "Aspartato aminotransferase",
        ),
        "1",
        "5000",
    ),
    Entry(
        "tgp",
        "TGP (ALT)",
        "liver",
        "U/L",
        (
            "Transaminase glutâmico pirúvica (TGP/ALT)",
            "TGP/ALT",
            "TGP",
            "ALT",
            "Alanina aminotransferase",
        ),
        "1",
        "5000",
    ),
    Entry(
        "fosfatase_alcalina",
        "Fosfatase alcalina",
        "liver",
        "U/L",
        ("Fosfatase alcalina", "FA"),
        "5",
        "2000",
    ),
    Entry(
        "ggt",
        "Gama-GT",
        "liver",
        "U/L",
        ("Gama GT", "Gama glutamil transferase", "GGT"),
        "1",
        "3000",
    ),
    Entry(
        "bilirrubina_total",
        "Bilirrubina total",
        "liver",
        "mg/dL",
        ("Bilirrubina total", "Bilirrubinas total"),
        "0.05",
        "40",
    ),
    Entry(
        "bilirrubina_direta",
        "Bilirrubina direta",
        "liver",
        "mg/dL",
        ("Bilirrubina direta", "Bilirrubinas direta"),
        "0.01",
        "30",
    ),
    Entry(
        "bilirrubina_indireta",
        "Bilirrubina indireta",
        "liver",
        "mg/dL",
        ("Bilirrubina indireta", "Bilirrubinas indireta"),
        "0.01",
        "30",
        calculated=True,
    ),
    Entry("albumina", "Albumina", "liver", "g/dL", ("Albumina",), "1", "7"),
    # Vitaminas
    Entry(
        "vitamina_b12",
        "Vitamina B12",
        "vitamins",
        "pg/mL",
        ("Vitamina B12", "B12", "Cobalamina"),
        "50",
        "3000",
        conversions=(("pmol/L", "1.355"),),
    ),
    Entry(
        "vitamina_d",
        "Vitamina D (25-OH)",
        "vitamins",
        "ng/mL",
        (
            "25-Hidroxi vitamina D",
            "Vitamina D",
            "Vitamina D 25-OH",
            "25-OH vitamina D",
            "25 - Hidroxivitamina D",
            "25 - Hidroxivitamina D3",
        ),
        "3",
        "200",
        conversions=(("nmol/L", "0.4006"),),
    ),
    Entry(
        "acido_folico", "Ácido fólico", "vitamins", "ng/mL", ("Ácido fólico", "Folato"), "0.5", "50"
    ),
    # Hormônios
    Entry(
        "tsh",
        "TSH",
        "hormones",
        "µUI/mL",
        ("TSH ultra sensível", "TSH"),
        "0.005",
        "200",
        conversions=(("mUI/L", "1"),),
    ),
    Entry("t4_livre", "T4 livre", "hormones", "ng/dL", ("T4 livre", "Tiroxina livre"), "0.1", "10"),
    Entry(
        "t3_total",
        "T3 total",
        "hormones",
        "ng/dL",
        ("T3 total", "T3", "Triiodotironina"),
        "10",
        "800",
    ),
    Entry(
        "t3_livre",
        "T3 livre",
        "hormones",
        "ng/dL",
        ("T3 livre", "Triiodotironina livre", "Triiodotironina (T3) livre"),
        "0.05",
        "10",
    ),
    Entry(
        "fsh",
        "FSH",
        "hormones",
        "mUI/mL",
        ("Hormônio folículo estimulante (FSH)", "FSH"),
        "0",
        "300",
        conversions=(("UI/L", "1"),),
    ),
    Entry(
        "lh",
        "LH",
        "hormones",
        "mUI/mL",
        ("Hormônio luteinizante (LH)", "LH", "LH - Específico", "LH específico"),
        "0",
        "300",
        conversions=(("UI/L", "1"),),
    ),
    Entry(
        "testosterona_total",
        "Testosterona total",
        "hormones",
        "ng/dL",
        ("Testosterona total", "Testosterona"),
        "1",
        "3000",
        conversions=(("nmol/L", "28.84"),),
    ),
    Entry(
        "testosterona_livre",
        "Testosterona livre",
        "hormones",
        "ng/dL",
        ("Testosterona livre calculada", "Testosterona livre"),
        "0.01",
        "100",
        calculated=True,
        conversions=(("pmol/L", "0.028842"),),
    ),
    Entry(
        "shbg",
        "SHBG",
        "hormones",
        "nmol/L",
        ("SHBG", "Globulina ligadora de hormônios sexuais"),
        "1",
        "300",
    ),
    Entry("cortisol", "Cortisol", "hormones", "µg/dL", ("Cortisol",), "0.5", "80"),
    Entry("prolactina", "Prolactina", "hormones", "ng/mL", ("Prolactina",), "0.5", "500"),
    Entry("estradiol", "Estradiol", "hormones", "pg/mL", ("Estradiol",), "1", "5000"),
    # Ferro
    Entry("ferritina", "Ferritina", "iron", "ng/mL", ("Ferritina",), "1", "5000"),
    Entry("ferro_serico", "Ferro sérico", "iron", "µg/dL", ("Ferro sérico", "Ferro"), "5", "500"),
    Entry(
        "saturacao_transferrina",
        "Saturação da transferrina",
        "iron",
        "%",
        ("Saturação da transferrina", "Índice de saturação da transferrina"),
        "1",
        "100",
        calculated=True,
    ),
    # Inflamação
    Entry(
        "pcr",
        "Proteína C reativa",
        "inflammation",
        "mg/L",
        ("Proteína C reativa", "PCR", "PCR ultrassensível"),
        "0.01",
        "500",
    ),
    Entry(
        "vhs",
        "VHS",
        "inflammation",
        "mm/h",
        (
            "VHS",
            "Velocidade de hemossedimentação",
            "Eritrossedimentação na 1ª hora",
            "Hemossedimentação",
        ),
        "0",
        "150",
    ),
    # Marcadores tumorais
    Entry(
        "psa_total",
        "PSA total",
        "tumor_markers",
        "ng/mL",
        (
            "PSA - Antígeno prostático específico",
            "PSA",
            "PSA total",
            "Antígeno prostático específico",
        ),
        "0",
        "1000",
    ),
    # Exames do laboratório Softl@b que o catálogo ainda não tinha. Sem entrada própria, o
    # fuzzy trocaria o nome por outro parecido (Vitamina A -> D, Mielócitos -> Monócitos).
    Entry(
        "glicose_pos_prandial",
        "Glicose pós-prandial",
        "glycemia",
        "mg/dL",
        ("Glicose pós prandial", "Glicemia pós-prandial", "Glicemia pós prandial"),
        "20",
        "800",
        conversions=(("mmol/L", "18.016"),),
    ),
    Entry(
        "proteinas_totais", "Proteínas totais", "liver", "g/dL", ("Proteínas totais",), "2", "12"
    ),
    Entry(
        "reserva_alcalina",
        "Reserva alcalina",
        "electrolytes",
        "mEq/L",
        ("Reserva alcalina",),
        "5",
        "50",
    ),
    Entry("lipase", "Lipase", "other", "U/L", ("Lipase",), "0", "5000"),
    Entry("amilase", "Amilase", "other", "U/L", ("Amilase",), "0", "5000"),
    Entry(
        "cpk",
        "Creatinofosfoquinase (CPK)",
        "other",
        "U/L",
        ("Creatinofosfoquinase - CPK", "Creatinofosfoquinase", "CPK", "CK", "Creatina quinase"),
        "0",
        "100000",
    ),
    Entry(
        "ldh",
        "Desidrogenase lática (LDH)",
        "other",
        "U/L",
        (
            "Desidrogenase lática",
            "Desidrogenase láctica",
            "Dehidrogenase lática",
            "Dehidrogenase láctica",
            "LDH",
            "DHL",
        ),
        "0",
        "10000",
    ),
    Entry(
        "fibrinogenio",
        "Fibrinogênio",
        "other",
        "mg/dL",
        ("Fibrinogênio", "Dosagem do fibrinogênio"),
        "50",
        "1500",
    ),
    Entry("progesterona", "Progesterona", "hormones", "ng/mL", ("Progesterona",), "0", "200"),
    Entry("estrona", "Estrona", "hormones", "pg/mL", ("Estrona",), "1", "3000"),
    Entry(
        "dihidrotestosterona",
        "Di-hidrotestosterona",
        "hormones",
        "pg/mL",
        ("Dihidrotestosterona", "DHT"),
        "10",
        "5000",
    ),
    Entry(
        "acth", "ACTH", "hormones", "pg/mL", ("ACTH", "Hormônio adrenocorticotrófico"), "1", "2000"
    ),
    Entry(
        "dhea_s",
        "Sulfato de DHEA",
        "hormones",
        "µg/dL",
        ("Sulfato de DHEA", "DHEA-S", "Sulfato de dehidroepiandrosterona"),
        "1",
        "2000",
    ),
    Entry(
        "pth",
        "Paratormônio (PTH)",
        "hormones",
        "pg/mL",
        ("Paratormônio (molécula inteira)", "Paratormônio", "PTH"),
        "1",
        "3000",
    ),
    Entry(
        "peptideo_c",
        "Peptídeo C",
        "glycemia",
        "ng/mL",
        ("Peptídeo C", 'Peptídeo "C"'),
        "0.1",
        "50",
    ),
    Entry("t3_reverso", "T3 reverso", "hormones", "ng/mL", ("T3 reverso",), "0.01", "10"),
    Entry(
        "igf1",
        "IGF-1 (somatomedina C)",
        "hormones",
        "ng/mL",
        ("IGF-1 somatomedina C", "IGF-1", "Somatomedina C"),
        "5",
        "1500",
    ),
    Entry(
        "igfbp3",
        "IGFBP-3",
        "hormones",
        "µg/mL",
        ("IGFBP-3 proteína ligadora-3 do IGF", "IGFBP-3", "Proteína ligadora 3 do IGF"),
        "0.1",
        "30",
    ),
    Entry(
        "apolipoproteina_a1",
        "Apolipoproteína A-1",
        "lipids",
        "mg/dL",
        ("Apolipoproteína A", "Apolipoproteína A-1", "Apoliproteína A", "Apo A-1"),
        "20",
        "500",
    ),
    Entry(
        "apolipoproteina_b",
        "Apolipoproteína B",
        "lipids",
        "mg/dL",
        ("Apolipoproteína B", "Apoliproteína B", "Apo B"),
        "10",
        "400",
    ),
    Entry("vitamina_a", "Vitamina A", "vitamins", "mg/L", ("Vitamina A",), "0", "10"),
    Entry("vitamina_e", "Vitamina E", "vitamins", "mg/L", ("Vitamina E",), "0", "100"),
    Entry("homocisteina", "Homocisteína", "other", "µmol/L", ("Homocisteína",), "1", "300"),
    Entry("zinco", "Zinco", "other", "µg/dL", ("Zinco", "Zinco sérico"), "10", "500"),
    Entry("cobre", "Cobre", "other", "µg/dL", ("Cobre", "Cobre sérico"), "10", "500"),
    Entry("cromo", "Cromo", "other", "µg/L", ("Cromo",), "0", "100"),
    Entry("selenio", "Selênio", "other", "µg/L", ("Selênio", "Selênio sérico"), "5", "500"),
    Entry(
        "anti_tpo",
        "Anti-TPO",
        "other",
        "UI/mL",
        (
            "Anti-peroxidase ou anti-TPO",
            "Anti-TPO",
            "Anticorpos anti-peroxidase tiroidiana",
            "Anticorpos anti-TPO",
        ),
        "0",
        "10000",
    ),
    Entry(
        "anti_tireoglobulina",
        "Anti-tireoglobulina",
        "other",
        "UI/mL",
        (
            "Anticorpo anti-tireoglobulina",
            "Anti-tireoglobulina",
            "Anticorpos anti-tireoglobulina",
            "Anticorpos anti-tiroglobulina",
            "Anti-TG",
        ),
        "0",
        "10000",
    ),
    Entry("psa_livre", "PSA livre", "tumor_markers", "ng/mL", ("PSA livre",), "0", "1000"),
]


def sync_catalog(analyte_model, alias_model, conversion_model) -> int:
    """Cria/atualiza o catálogo sem apagar o que o usuário acrescentou.

    Em lote (uma dúzia de consultas, não milhares): com o banco longe (Neon a ~200 ms por ida e
    volta) o sync linha a linha levava minutos. Mesmo comportamento de antes: idempotente, só
    escreve o que mudou, nunca toca apelido de usuário nem re-aponta um apelido que já existe
    (quem aparece primeiro no catálogo fica com ele).
    """
    from django.db import transaction
    from django.utils import timezone

    has_user = any(f.name == "user" for f in alias_model._meta.get_fields())
    has_updated_at = any(f.name == "updated_at" for f in analyte_model._meta.get_fields())
    now = timezone.now()

    def decimal_or_none(value):
        # Na precisão da coluna (4 casas): `Decimal(0.1)` != `Decimal("0.1000")` do banco, e a
        # comparação "mudou?" precisa enxergar os dois como iguais.
        return None if value is None else Decimal(value).quantize(Decimal("0.0001"))

    with transaction.atomic():
        # --- exames ---------------------------------------------------------------------
        existing = {
            a.slug: a for a in analyte_model.objects.filter(slug__in=[e.slug for e in CATALOG])
        }
        to_create, to_update = [], []
        analyte_fields = (
            "name_pt",
            "category",
            "kind",
            "canonical_unit",
            "is_calculated",
            "plausible_min",
            "plausible_max",
            "sort_order",
        )
        for order, entry in enumerate(CATALOG):
            wanted = {
                "name_pt": entry.name,
                "category": entry.category,
                "kind": entry.kind,
                "canonical_unit": entry.unit,
                "is_calculated": entry.calculated,
                "plausible_min": decimal_or_none(entry.minimum),
                "plausible_max": decimal_or_none(entry.maximum),
                "sort_order": order,
            }
            current = existing.get(entry.slug)
            if current is None:
                to_create.append(analyte_model(slug=entry.slug, **wanted))
            elif any(getattr(current, name) != value for name, value in wanted.items()):
                for name, value in wanted.items():
                    setattr(current, name, value)
                if has_updated_at:
                    current.updated_at = now
                to_update.append(current)
        if to_create:
            analyte_model.objects.bulk_create(to_create)
        if to_update:
            analyte_model.objects.bulk_update(
                to_update, [*analyte_fields, *(["updated_at"] if has_updated_at else [])]
            )
        analytes = {
            a.slug: a for a in analyte_model.objects.filter(slug__in=[e.slug for e in CATALOG])
        }

        # --- apelidos globais (o de usuário nunca é tocado) ---------------------------------
        scope = alias_model.objects.filter(user__isnull=True) if has_user else alias_model.objects
        taken = set(scope.values_list("alias_normalized", flat=True))
        new_aliases = []
        for entry in CATALOG:
            keys = {normalize_name(entry.name), *(normalize_name(a) for a in entry.aliases)}
            keys |= {sorted_tokens_key(k) for k in keys}
            for key in sorted(keys):
                if not key or key in taken:
                    continue
                taken.add(key)  # o primeiro do catálogo fica com o apelido, como antes
                extra = {"user": None} if has_user else {}
                new_aliases.append(
                    alias_model(
                        alias_normalized=key, analyte=analytes[entry.slug], source="seed", **extra
                    )
                )
        if new_aliases:
            alias_model.objects.bulk_create(new_aliases, ignore_conflicts=True)

        # --- conversões de unidade ------------------------------------------------------
        known = {
            (c.analyte_id, c.from_unit): c
            for c in conversion_model.objects.filter(analyte__in=list(analytes.values()))
        }
        conv_create, conv_update = [], []
        for entry in CATALOG:
            analyte = analytes[entry.slug]
            for from_unit, factor in entry.conversions:
                wanted_factor = Decimal(factor).quantize(Decimal("0.00000001"))  # 8 casas
                current = known.get((analyte.pk, from_unit))
                if current is None:
                    conv_create.append(
                        conversion_model(
                            analyte=analyte,
                            from_unit=from_unit,
                            to_unit=entry.unit,
                            factor=wanted_factor,
                        )
                    )
                elif current.to_unit != entry.unit or current.factor != wanted_factor:
                    current.to_unit, current.factor = entry.unit, wanted_factor
                    if has_updated_at:
                        current.updated_at = now
                    conv_update.append(current)
        if conv_create:
            conversion_model.objects.bulk_create(conv_create, ignore_conflicts=True)
        if conv_update:
            conversion_model.objects.bulk_update(
                conv_update, ["to_unit", "factor", *(["updated_at"] if has_updated_at else [])]
            )
    return len(CATALOG)
