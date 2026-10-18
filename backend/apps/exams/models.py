from django.conf import settings
from django.db import models


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Sex(models.TextChoices):
    MALE = "male", "Masculino"
    FEMALE = "female", "Feminino"


class Profile(TimeStampedModel):
    """Sexo e nascimento escolhem a faixa de referência aplicável ao resultado."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="exam_profile"
    )
    sex = models.CharField(max_length=10, choices=Sex.choices, blank=True)
    birth_date = models.DateField(null=True, blank=True)

    class Meta:
        verbose_name = "Perfil"
        verbose_name_plural = "Perfis"

    def __str__(self) -> str:
        return f"Perfil de {self.user_id}"


class Laboratory(TimeStampedModel):
    name = models.CharField("Nome", max_length=255)
    normalized_name = models.CharField(max_length=255, unique=True)
    # Dicas de layout aprendidas após o primeiro laudo bem-sucedido (fase 4).
    layout_hints = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = "Laboratório"
        verbose_name_plural = "Laboratórios"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class DocumentStatus(models.TextChoices):
    UPLOADED = "uploaded", "Enviado"
    EXTRACTING = "extracting", "Extraindo"
    NEEDS_REVIEW = "needs_review", "Revisão necessária"
    COMPLETED = "completed", "Concluído"
    FAILED = "failed", "Falhou"


class ExtractionMethod(models.TextChoices):
    TEXT = "text", "Camada de texto"
    VISION = "vision", "Visão"
    MIXED = "mixed", "Misto"


class ExamDocument(TimeStampedModel):
    """Um laudo enviado (o PDF em si é descartado ao fim da extração)."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="exam_documents"
    )
    laboratory = models.ForeignKey(
        Laboratory,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="documents",
        verbose_name="Laboratório",
    )
    sha256 = models.CharField(max_length=64)
    original_filename = models.CharField("Arquivo", max_length=255)
    page_count = models.PositiveIntegerField(default=0)
    # Progresso da extração, em blocos de exame (0/0 até a segmentação terminar).
    progress_done = models.PositiveIntegerField(default=0)
    progress_total = models.PositiveIntegerField(default=0)
    status = models.CharField(
        max_length=20, choices=DocumentStatus.choices, default=DocumentStatus.UPLOADED
    )
    extraction_method = models.CharField(
        max_length=10, choices=ExtractionMethod.choices, blank=True
    )

    accession_number = models.CharField("Número do pedido", max_length=64, blank=True)
    ordering_physician = models.CharField("Médico solicitante", max_length=255, blank=True)
    patient_name_on_doc = models.CharField(max_length=255, blank=True)
    patient_birth_date = models.DateField(null=True, blank=True)
    collected_on = models.DateField("Coletado em", null=True, blank=True)
    issued_at = models.DateTimeField("Emitido em", null=True, blank=True)

    # Texto de cada página (camada de texto ou transcrição por visão). Fica no
    # banco para reprocessar sem reler o PDF, que é descartado. Contém dado de
    # saúde: nunca logar.
    page_text = models.JSONField(default=list, blank=True)
    # Caminho temporário do PDF; vazio assim que a extração termina.
    upload_path = models.CharField(max_length=500, blank=True)

    error_code = models.CharField(max_length=50, blank=True)
    error_params = models.JSONField(default=dict, blank=True)
    # [{"code": "...", "params": {...}}] — avisos do laudo inteiro.
    warnings = models.JSONField(default=list, blank=True)

    class Meta:
        verbose_name = "Laudo"
        verbose_name_plural = "Laudos"
        ordering = ["-collected_on", "-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["user", "sha256"], name="unique_document_per_user"),
            models.UniqueConstraint(
                fields=["user", "laboratory", "accession_number"],
                condition=~models.Q(accession_number=""),
                name="unique_accession_per_user_lab",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.original_filename} ({self.status})"


class RunStatus(models.TextChoices):
    QUEUED = "queued", "Na fila"
    RUNNING = "running", "Executando"
    SUCCESS = "success", "Sucesso"
    FAILED = "failed", "Falhou"


class ExtractionRun(TimeStampedModel):
    """Histórico de execuções da extração (análogo ao SyncJob do finances)."""

    document = models.ForeignKey(ExamDocument, on_delete=models.CASCADE, related_name="runs")
    status = models.CharField(max_length=20, choices=RunStatus.choices, default=RunStatus.QUEUED)
    model = models.CharField(max_length=100, blank=True)
    vision_model = models.CharField(max_length=100, blank=True)
    prompt_version = models.CharField(max_length=20, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    error_code = models.CharField(max_length=50, blank=True)
    error_params = models.JSONField(default=dict, blank=True)
    # Contagens apenas (blocos, linhas, avisos): nunca valores de exame.
    stats = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = "Execução de extração"
        verbose_name_plural = "Execuções de extração"
        ordering = ["-created_at"]


class AnalyteCategory(models.TextChoices):
    HEMATOLOGY = "hematology", "Hematologia"
    GLYCEMIA = "glycemia", "Glicemia"
    LIPIDS = "lipids", "Lipidograma"
    KIDNEY = "kidney", "Função renal"
    LIVER = "liver", "Função hepática"
    ELECTROLYTES = "electrolytes", "Eletrólitos"
    HORMONES = "hormones", "Hormônios"
    VITAMINS = "vitamins", "Vitaminas"
    IRON = "iron", "Ferro"
    INFLAMMATION = "inflammation", "Inflamação"
    TUMOR_MARKERS = "tumor_markers", "Marcadores tumorais"
    OTHER = "other", "Outros"


class AnalyteKind(models.TextChoices):
    NUMERIC = "numeric", "Numérico"
    QUALITATIVE = "qualitative", "Qualitativo"


class Analyte(TimeStampedModel):
    """Exame canônico (ex.: Glicose), comum a todos os laboratórios."""

    slug = models.SlugField(unique=True)
    name_pt = models.CharField("Nome", max_length=255)
    name_en = models.CharField(max_length=255, blank=True)
    category = models.CharField(
        "Categoria", max_length=20, choices=AnalyteCategory.choices, default=AnalyteCategory.OTHER
    )
    kind = models.CharField(max_length=15, choices=AnalyteKind.choices, default=AnalyteKind.NUMERIC)
    canonical_unit = models.CharField("Unidade canônica", max_length=40, blank=True)
    is_calculated = models.BooleanField(default=False)
    loinc_code = models.CharField(max_length=20, blank=True)
    # Limites de plausibilidade (na unidade canônica): fora deles, revisão.
    plausible_min = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    plausible_max = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    sort_order = models.PositiveIntegerField(default=1000)

    class Meta:
        verbose_name = "Exame"
        verbose_name_plural = "Exames"
        ordering = ["category", "sort_order", "name_pt"]

    def __str__(self) -> str:
        return self.name_pt


class AliasSource(models.TextChoices):
    SEED = "seed", "Catálogo"
    LLM = "llm", "Modelo"
    USER = "user", "Usuário"


class AnalyteAlias(TimeStampedModel):
    """Apelido de exame. `user` nulo = apelido global (seed); senão, é do usuário."""

    analyte = models.ForeignKey(Analyte, on_delete=models.CASCADE, related_name="aliases")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="analyte_aliases",
    )
    alias_normalized = models.CharField(max_length=255)
    source = models.CharField(max_length=10, choices=AliasSource.choices, default=AliasSource.SEED)

    class Meta:
        verbose_name = "Apelido de exame"
        verbose_name_plural = "Apelidos de exames"
        constraints = [
            models.UniqueConstraint(
                fields=["alias_normalized"],
                condition=models.Q(user__isnull=True),
                name="unique_global_alias",
            ),
            models.UniqueConstraint(
                fields=["user", "alias_normalized"],
                condition=models.Q(user__isnull=False),
                name="unique_user_alias",
            ),
        ]


class UnitConversion(TimeStampedModel):
    analyte = models.ForeignKey(Analyte, on_delete=models.CASCADE, related_name="conversions")
    from_unit = models.CharField(max_length=40)
    to_unit = models.CharField(max_length=40)
    factor = models.DecimalField(max_digits=18, decimal_places=8)

    class Meta:
        verbose_name = "Conversão de unidade"
        verbose_name_plural = "Conversões de unidade"
        constraints = [
            models.UniqueConstraint(fields=["analyte", "from_unit"], name="unique_conversion")
        ]


class Comparator(models.TextChoices):
    EQ = "eq", "="
    LT = "lt", "<"
    LTE = "lte", "≤"
    GT = "gt", ">"
    GTE = "gte", "≥"


class ResultOrigin(models.TextChoices):
    CURRENT = "current", "Do laudo"
    LAB_PREVIOUS = "lab_previous", "Anteriores impressos"


class ReviewStatus(models.TextChoices):
    AUTO = "auto", "Automático"
    NEEDS_REVIEW = "needs_review", "Revisão necessária"
    CONFIRMED = "confirmed", "Confirmado"
    USER_CORRECTED = "user_corrected", "Corrigido"


class ResultFlag(models.TextChoices):
    LOW = "low", "Abaixo da referência"
    NORMAL = "normal", "Dentro da referência"
    HIGH = "high", "Acima da referência"
    ABNORMAL = "abnormal", "Alterado"
    UNKNOWN = "unknown", "Sem referência"


class ReferenceKind(models.TextChoices):
    RANGE = "range", "Faixa"
    UPPER = "upper", "Limite superior"
    LOWER = "lower", "Limite inferior"
    QUALITATIVE = "qualitative", "Qualitativo"
    NONE = "none", "Sem referência"


class ExamResult(TimeStampedModel):
    """Um valor medido. Guarda o impresso (`*_raw`) e o normalizado lado a lado."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="exam_results"
    )
    document = models.ForeignKey(ExamDocument, on_delete=models.CASCADE, related_name="results")
    analyte = models.ForeignKey(
        Analyte,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="results",
        verbose_name="Exame",
    )
    origin = models.CharField(
        max_length=15, choices=ResultOrigin.choices, default=ResultOrigin.CURRENT
    )
    page = models.PositiveIntegerField(default=1)
    position = models.PositiveIntegerField(default=0)

    # Como impresso
    name_raw = models.CharField(max_length=255)
    section_raw = models.CharField(max_length=255, blank=True)
    value_raw = models.CharField(max_length=255, blank=True)
    unit_raw = models.CharField(max_length=60, blank=True)
    reference_raw = models.TextField(blank=True)
    flag_printed = models.CharField(max_length=20, blank=True)

    # Normalizado. O comparador é separado do número ("<0,5" -> lt + 0,5).
    comparator = models.CharField(max_length=4, choices=Comparator.choices, default=Comparator.EQ)
    value_numeric = models.DecimalField(max_digits=20, decimal_places=6, null=True, blank=True)
    value_text = models.CharField(max_length=255, blank=True)
    unit = models.CharField(max_length=40, blank=True)
    value_canonical = models.DecimalField(max_digits=20, decimal_places=6, null=True, blank=True)

    # Referência do próprio resultado: varia por laboratório, sexo e idade.
    ref_kind = models.CharField(
        max_length=12, choices=ReferenceKind.choices, default=ReferenceKind.NONE
    )
    ref_low = models.DecimalField(max_digits=20, decimal_places=6, null=True, blank=True)
    ref_high = models.DecimalField(max_digits=20, decimal_places=6, null=True, blank=True)
    ref_low_inclusive = models.BooleanField(default=True)
    ref_high_inclusive = models.BooleanField(default=True)
    ref_bands = models.JSONField(default=list, blank=True)
    flag = models.CharField(max_length=10, choices=ResultFlag.choices, default=ResultFlag.UNKNOWN)
    interpretation = models.CharField(max_length=255, blank=True)

    collected_at = models.DateTimeField("Coletado em", null=True, blank=True)
    method = models.CharField(max_length=255, blank=True)
    specimen = models.CharField(max_length=100, blank=True)
    performing_lab = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)

    review_status = models.CharField(
        max_length=15, choices=ReviewStatus.choices, default=ReviewStatus.AUTO
    )
    # [{"code": "...", "params": {...}}]
    warnings = models.JSONField(default=list, blank=True)
    source_line = models.TextField(blank=True)
    raw = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = "Resultado"
        verbose_name_plural = "Resultados"
        ordering = ["document_id", "page", "position"]
        indexes = [
            models.Index(fields=["user", "analyte", "collected_at"]),
            models.Index(fields=["document", "position"]),
        ]

    def __str__(self) -> str:
        return f"{self.name_raw}: {self.value_raw}"
