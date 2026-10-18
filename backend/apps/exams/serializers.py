import re
from datetime import datetime, time
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.dateparse import parse_date
from rest_framework import serializers

from .codes import ErrorCode
from .errors import CodedAPIError
from .extraction.numbers import parse_number
from .extraction.softlab_lines import is_plain_value, is_qualitative_text
from .models import (
    Analyte,
    ExamDocument,
    ExamResult,
    ExtractionRun,
    Laboratory,
    Profile,
    ReviewStatus,
)

LABORATORY_NAME_MAX_LENGTH = Laboratory._meta.get_field("name").max_length


class UserScopedPrimaryKeyRelatedField(serializers.PrimaryKeyRelatedField):
    """FK que só aceita objetos do próprio usuário autenticado.

    O ModelSerializer valida FK contra a tabela inteira; sem este filtro um
    usuário poderia apontar para o recurso de outro. `user_path` é o lookup do
    dono a partir do modelo relacionado.
    """

    def __init__(self, *args, user_path: str, **kwargs):
        self.user_path = user_path
        super().__init__(*args, **kwargs)

    def get_queryset(self):
        queryset = super().get_queryset()
        request = self.context.get("request")
        if request is None or not request.user.is_authenticated:
            return queryset.none()
        return queryset.filter(**{self.user_path: request.user})


class RegisterSerializer(serializers.Serializer):
    # 150 = limite do `username` do Django (o e-mail é usado como username).
    email = serializers.EmailField(max_length=150)
    password = serializers.CharField(write_only=True, min_length=8)
    first_name = serializers.CharField(required=False, allow_blank=True)
    last_name = serializers.CharField(required=False, allow_blank=True)

    def validate_email(self, value):
        if get_user_model().objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError(ErrorCode.EMAIL_ALREADY_REGISTERED.value)
        return value

    def validate(self, attrs):
        candidate = get_user_model()(
            username=attrs["email"],
            email=attrs["email"],
            first_name=attrs.get("first_name", ""),
            last_name=attrs.get("last_name", ""),
        )
        try:
            validate_password(attrs["password"], user=candidate)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"password": list(exc.messages)}) from exc
        return attrs

    def create(self, validated_data):
        return get_user_model().objects.create_user(
            username=validated_data["email"],
            email=validated_data["email"],
            password=validated_data["password"],
            first_name=validated_data.get("first_name", ""),
            last_name=validated_data.get("last_name", ""),
        )


class ProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = Profile
        fields = ("sex", "birth_date")


class AnalyteSerializer(serializers.ModelSerializer):
    name = serializers.CharField(source="name_pt")
    unit = serializers.CharField(source="canonical_unit")

    class Meta:
        model = Analyte
        fields = ("id", "slug", "name", "category", "kind", "unit", "is_calculated")


class AnalyteMiniSerializer(serializers.ModelSerializer):
    name = serializers.CharField(source="name_pt")
    unit = serializers.CharField(source="canonical_unit")

    class Meta:
        model = Analyte
        fields = ("id", "slug", "name", "unit")


class ExtractionRunSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExtractionRun
        fields = (
            "id",
            "status",
            "model",
            "vision_model",
            "prompt_version",
            "started_at",
            "finished_at",
            "error_code",
            "error_params",
            "stats",
        )


class ExamDocumentSerializer(serializers.ModelSerializer):
    laboratory_name = serializers.CharField(source="laboratory.name", default="", read_only=True)
    result_count = serializers.IntegerField(read_only=True, default=0)
    review_count = serializers.IntegerField(read_only=True, default=0)
    out_of_range_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = ExamDocument
        fields = (
            "id",
            "original_filename",
            "status",
            "extraction_method",
            "page_count",
            "progress_done",
            "progress_total",
            "laboratory",
            "laboratory_name",
            "accession_number",
            "ordering_physician",
            "collected_on",
            "issued_at",
            "error_code",
            "error_params",
            "warnings",
            "result_count",
            "review_count",
            "out_of_range_count",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class ExamDocumentDetailSerializer(ExamDocumentSerializer):
    page_text = serializers.SerializerMethodField()

    class Meta(ExamDocumentSerializer.Meta):
        fields = (*ExamDocumentSerializer.Meta.fields, "page_text")
        read_only_fields = fields

    def get_page_text(self, obj):
        return obj.page_text


class LaboratorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Laboratory
        fields = ("id", "name")
        read_only_fields = fields


class DocumentLaboratorySerializer(serializers.Serializer):
    """Entrada do PATCH do laudo: só `laboratory_name` é gravável.

    Os espaços das pontas são cortados e os do meio colapsados; vazio limpa o laboratório.
    O limite de tamanho vira erro com código (sem texto customizado do backend).
    """

    laboratory_name = serializers.CharField(allow_blank=True, trim_whitespace=False)

    def validate_laboratory_name(self, value: str) -> str:
        name = " ".join(value.split())
        if len(name) > LABORATORY_NAME_MAX_LENGTH:
            raise CodedAPIError(
                ErrorCode.LABORATORY_NAME_TOO_LONG, {"max": LABORATORY_NAME_MAX_LENGTH}
            )
        return name


EDITABLE_REVIEW = {ReviewStatus.CONFIRMED}


class ExamResultSerializer(serializers.ModelSerializer):
    analyte = serializers.PrimaryKeyRelatedField(
        queryset=Analyte.objects.all(), required=False, allow_null=True
    )
    analyte_info = AnalyteMiniSerializer(source="analyte", read_only=True)
    value_raw = serializers.CharField(max_length=255, required=False, allow_blank=True)
    unit_raw = serializers.CharField(max_length=60, required=False, allow_blank=True)
    reference_raw = serializers.CharField(max_length=4000, required=False, allow_blank=True)
    notes = serializers.CharField(max_length=4000, required=False, allow_blank=True)

    class Meta:
        model = ExamResult
        fields = (
            "id",
            "document",
            "analyte",
            "analyte_info",
            "origin",
            "page",
            "position",
            "name_raw",
            "section_raw",
            "value_raw",
            "unit_raw",
            "reference_raw",
            "flag_printed",
            "comparator",
            "value_numeric",
            "value_text",
            "unit",
            "value_canonical",
            "ref_kind",
            "ref_low",
            "ref_high",
            "ref_low_inclusive",
            "ref_high_inclusive",
            "ref_bands",
            "flag",
            "interpretation",
            "collected_at",
            "method",
            "specimen",
            "performing_lab",
            "notes",
            "review_status",
            "warnings",
            "source_line",
            "created_at",
            "updated_at",
        )
        read_only_fields = tuple(
            f
            for f in fields
            if f
            not in (
                "analyte",
                "value_raw",
                "unit_raw",
                "reference_raw",
                "collected_at",
                "notes",
                "review_status",
            )
        )

    def validate_review_status(self, value):
        if value not in EDITABLE_REVIEW:
            raise serializers.ValidationError("invalid")
        return value


# Cabe nas colunas DecimalField(20, 6) do banco (14 dígitos inteiros).
MAX_ABS_DECIMAL = Decimal(10) ** 14
_DATE_ONLY = re.compile(r"\d{4}-\d{2}-\d{2}")


class DateOrDateTimeField(serializers.DateTimeField):
    """Data (`2026-08-25`, vira meia-noite local) ou data e hora ISO."""

    def to_internal_value(self, value):
        if isinstance(value, str) and _DATE_ONLY.fullmatch(value.strip()):
            day = parse_date(value.strip())
            if day is not None:
                return self.enforce_timezone(datetime.combine(day, time.min))
        return super().to_internal_value(value)


def _collapse(value: str) -> str:
    return " ".join(value.split())


class ManualResultSerializer(serializers.Serializer):
    """Entrada do POST /exam-results/: só o que o usuário digita.

    Tudo que é derivado (número, unidade canônica, faixa, sinalização, avisos) o servidor
    calcula com as funções da importação. Erros de campo são códigos (`errors.<código>`).
    """

    document = UserScopedPrimaryKeyRelatedField(
        queryset=ExamDocument.objects.all(), user_path="user"
    )
    analyte = serializers.PrimaryKeyRelatedField(
        queryset=Analyte.objects.all(), required=False, allow_null=True
    )
    name_raw = serializers.CharField(
        max_length=255, required=False, allow_blank=True, trim_whitespace=False, default=""
    )
    value_raw = serializers.CharField(
        max_length=255, required=False, allow_blank=True, trim_whitespace=False, default=""
    )
    unit_raw = serializers.CharField(max_length=60, required=False, allow_blank=True, default="")
    reference_raw = serializers.CharField(
        max_length=4000, required=False, allow_blank=True, default=""
    )
    collected_at = DateOrDateTimeField(required=False, allow_null=True)
    notes = serializers.CharField(max_length=4000, required=False, allow_blank=True, default="")

    def validate_name_raw(self, value: str) -> str:
        name = _collapse(value)
        if not name:
            raise serializers.ValidationError(ErrorCode.RESULT_NAME_REQUIRED.value)
        return name

    def validate_value_raw(self, value: str) -> str:
        text = _collapse(value)
        if not text:
            raise serializers.ValidationError(ErrorCode.RESULT_VALUE_REQUIRED.value)
        # Número (vírgula ou ponto, com `<`/`>` opcional) ou o vocabulário qualitativo fechado
        # do laudo (Negativo, Positivo, Reagente...). Unidade vai no campo próprio.
        if not (is_plain_value(text) or is_qualitative_text(text)):
            raise serializers.ValidationError(ErrorCode.RESULT_VALUE_INVALID.value)
        parsed = parse_number(text)
        if parsed.value is not None and abs(parsed.value) >= MAX_ABS_DECIMAL:
            raise serializers.ValidationError(ErrorCode.RESULT_VALUE_INVALID.value)
        return text

    def validate_unit_raw(self, value: str) -> str:
        return _collapse(value)


class DismissWarningSerializer(serializers.Serializer):
    """Entrada de POST /exam-documents/<id>/dismiss-warning/ (`title`: opcional, laudos antigos)."""

    code = serializers.CharField(max_length=50)
    page = serializers.IntegerField(min_value=1, max_value=100000)
    title = serializers.CharField(max_length=255, required=False, allow_blank=True)
