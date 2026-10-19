import logging
from datetime import date, datetime, time

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Count, Max, Q
from django.utils import timezone
from rest_framework import mixins, permissions, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView

from .codes import ErrorCode, WarningCode
from .errors import CodedAPIError
from .extraction.analytes import AnalyteResolver, learn_alias
from .extraction.manual import build_manual_result, duplicate_key
from .extraction.pipeline import find_accession_clash, laboratory_for
from .extraction.recompute import recompute_result
from .history import build_analyte_history, build_overview, build_report
from .models import (
    CURRENT_ORIGINS,
    Analyte,
    DocumentStatus,
    ExamDocument,
    ExamResult,
    Laboratory,
    Profile,
    ResultFlag,
    ResultOrigin,
    ReviewStatus,
)
from .pagination import UnboundedCollectionPagination
from .serializers import (
    MAX_ABS_DECIMAL,
    AnalyteSerializer,
    DismissWarningSerializer,
    DocumentLaboratorySerializer,
    ExamDocumentDetailSerializer,
    ExamDocumentSerializer,
    ExamResultSerializer,
    ExtractionRunSerializer,
    LaboratorySerializer,
    ManualResultSerializer,
    ProfileSerializer,
    RegisterSerializer,
)
from .storage import PDF_MAGIC, delete_upload, save_upload
from .tasks import process_exam_document, sweep_orphans

logger = logging.getLogger(__name__)

OUT_OF_RANGE = [ResultFlag.LOW, ResultFlag.HIGH, ResultFlag.ABNORMAL]

# Margem sobre o limite do arquivo para o envelope multipart (cabeçalhos, campos).
UPLOAD_ENVELOPE_BYTES = 1024 * 1024
MAX_DB_INTEGER = 2**63 - 1


def _int_param(params, name: str) -> int | None:
    """Inteiro válido da query string; inválido ou vazio é ignorado (nunca 500)."""
    raw = params.get(name)
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if 0 <= value <= MAX_DB_INTEGER else None


def _date_param(params, name: str) -> date | None:
    raw = params.get(name)
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _choice_param(params, name: str, choices) -> str | None:
    raw = params.get(name)
    return raw if raw and raw in choices.values else None


def _duplicate_error(existing: ExamDocument | None) -> CodedAPIError:
    when = (existing.collected_on or existing.created_at.date()) if existing else date.today()
    return CodedAPIError(ErrorCode.DUPLICATE_DOCUMENT, {"date": when.isoformat()}, status_code=409)


def _accession_conflict(clash: ExamDocument) -> CodedAPIError:
    return CodedAPIError(
        ErrorCode.LABORATORY_ACCESSION_CONFLICT, {"document_id": clash.pk}, status_code=409
    )


def _enqueue_extraction(document_id: int, *args) -> None:
    """Enfileira a extração após o commit; falha do broker marca o laudo como FAILED."""

    def run() -> None:
        try:
            process_exam_document.delay(document_id, *args)
        except Exception:
            logger.exception("Falha ao enfileirar a extração do laudo")
            _mark_enqueue_failed(document_id)

    transaction.on_commit(run)


def _sweep_stuck_documents() -> None:
    """Modo inline: sem worker nem agendador, a faxina de laudos presos roda em cada upload.

    Uma função cortada no meio da leitura (tempo esgotado, falha da plataforma) deixaria o laudo
    em "Extraindo" para sempre. Nunca derruba o upload.
    """
    try:
        sweep_orphans()
    except Exception as exc:
        logger.warning("Não foi possível varrer laudos presos (%s)", type(exc).__name__)


def _mark_enqueue_failed(document_id: int) -> None:
    document = ExamDocument.objects.filter(pk=document_id).first()
    if document is None:
        return
    delete_upload(document.upload_path)
    ExamDocument.objects.filter(pk=document_id).update(
        status=DocumentStatus.FAILED,
        error_code=str(ErrorCode.EXTRACTION_FAILED),
        error_params={},
        upload_path="",
    )


class HealthCheckAPIView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = []

    def get(self, request):
        return Response({"status": "ok"})


class ThrottledTokenObtainPairView(TokenObtainPairView):
    """Login com limite de tentativas (taxa `login`)."""

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"


class CurrentUserAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user = request.user
        profile, _ = Profile.objects.get_or_create(user=user)
        return Response(
            {
                "id": user.id,
                "email": user.email,
                "first_name": user.first_name,
                "last_name": user.last_name,
                # Papel e troca de senha pendente ficam fora do `profile`: é ele que o PUT
                # /profile/ grava, e o usuário nunca pode se promover por ali.
                "is_admin": profile.is_admin,
                "must_change_password": profile.must_change_password,
                "profile": ProfileSerializer(profile).data,
                # Limites que o frontend confere antes de enviar (a plataforma pode cortar o
                # corpo da requisição antes de o app responder com o `file_too_large`).
                "limits": {"max_upload_mb": settings.EXAMS_MAX_UPLOAD_BYTES // (1024 * 1024)},
            }
        )


class RegisterAPIView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request):
        # 404 (e não 403) para não confirmar que o endpoint existe.
        if not settings.ALLOW_PUBLIC_REGISTRATION:
            raise CodedAPIError(ErrorCode.REGISTRATION_DISABLED, status_code=404)
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return Response({"id": user.id, "email": user.email})


class ProfileAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        profile, _ = Profile.objects.get_or_create(user=request.user)
        return Response(ProfileSerializer(profile).data)

    def put(self, request):
        profile, _ = Profile.objects.get_or_create(user=request.user)
        serializer = ProfileSerializer(profile, data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class AnalyteViewSet(viewsets.ReadOnlyModelViewSet):
    """Catálogo global de exames (não pertence a usuário)."""

    queryset = Analyte.objects.all()
    serializer_class = AnalyteSerializer
    lookup_field = "slug"
    permission_classes = [permissions.IsAuthenticated]

    @action(detail=True, methods=["get"], url_path="history")
    def history(self, request, slug=None):
        analyte = self.get_object()
        return Response(build_analyte_history(request.user, analyte))


class LaboratoryListAPIView(APIView):
    """Laboratórios dos laudos do próprio usuário (sugestões de autocomplete).

    `Laboratory` é global; o escopo vem dos laudos de `request.user`, então nunca aparece
    laboratório que só outro usuário usa.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        used = ExamDocument.objects.filter(user=request.user, laboratory__isnull=False)
        laboratories = Laboratory.objects.filter(pk__in=used.values("laboratory_id")).order_by(
            "normalized_name", "id"
        )
        return Response(LaboratorySerializer(laboratories, many=True).data)


class OverviewAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        return Response(build_overview(request.user))


class ReportAPIView(APIView):
    """Dados para o relatório executivo (Markdown) montado no frontend."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        return Response(build_report(request.user))


def refresh_document_status(document: ExamDocument) -> None:
    # Nunca mexe em laudo em extração/enviado/falho: quem manda no status é o worker.
    if document.status not in (DocumentStatus.NEEDS_REVIEW, DocumentStatus.COMPLETED):
        return
    pending = document.results.filter(
        origin__in=CURRENT_ORIGINS, review_status=ReviewStatus.NEEDS_REVIEW
    ).exists()
    document.status = (
        DocumentStatus.NEEDS_REVIEW if pending or document.warnings else DocumentStatus.COMPLETED
    )
    document.save(update_fields=["status", "updated_at"])


class ExamDocumentViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = UnboundedCollectionPagination
    # JSON só para o PATCH do laboratório; o upload continua multipart.
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    # Sem PUT: o único campo editável do laudo é o laboratório (PATCH).
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_throttles(self):
        if self.action in ("create", "reprocess"):
            self.throttle_scope = "upload"
            return [ScopedRateThrottle()]
        return super().get_throttles()

    def get_serializer_class(self):
        return ExamDocumentDetailSerializer if self.action == "retrieve" else ExamDocumentSerializer

    def get_queryset(self):
        current = Q(results__origin__in=CURRENT_ORIGINS)
        queryset = (
            ExamDocument.objects.filter(user=self.request.user)
            .select_related("laboratory")
            .annotate(
                result_count=Count("results", filter=current),
                review_count=Count(
                    "results", filter=current & Q(results__review_status=ReviewStatus.NEEDS_REVIEW)
                ),
                out_of_range_count=Count(
                    "results", filter=current & Q(results__flag__in=OUT_OF_RANGE)
                ),
            )
        )
        queryset = queryset.order_by("-collected_on", "-created_at")
        params = self.request.query_params
        if (value := _choice_param(params, "status", DocumentStatus)) is not None:
            queryset = queryset.filter(status=value)
        if (laboratory := _int_param(params, "laboratory")) is not None:
            queryset = queryset.filter(laboratory_id=laboratory)
        if (collected_from := _date_param(params, "collected_from")) is not None:
            queryset = queryset.filter(collected_on__gte=collected_from)
        if (collected_to := _date_param(params, "collected_to")) is not None:
            queryset = queryset.filter(collected_on__lte=collected_to)
        if params.get("out_of_range") in ("1", "true"):
            queryset = queryset.filter(out_of_range_count__gt=0)
        return queryset

    def create(self, request):
        too_large = CodedAPIError(
            ErrorCode.FILE_TOO_LARGE,
            {"max_mb": settings.EXAMS_MAX_UPLOAD_BYTES // (1024 * 1024)},
            status_code=413,
        )
        # Rejeita pelo cabeçalho antes de ler o corpo.
        try:
            declared = int(request.META.get("CONTENT_LENGTH") or 0)
        except ValueError:
            declared = 0
        if declared > settings.EXAMS_MAX_UPLOAD_BYTES + UPLOAD_ENVELOPE_BYTES:
            raise too_large

        if settings.CELERY_TASK_ALWAYS_EAGER:
            _sweep_stuck_documents()
        upload = request.FILES.get("file")
        if upload is None:
            raise CodedAPIError(ErrorCode.FILE_MISSING)
        if upload.size > settings.EXAMS_MAX_UPLOAD_BYTES:
            raise too_large
        head = upload.read(len(PDF_MAGIC))
        upload.seek(0)
        if head != PDF_MAGIC:
            raise CodedAPIError(ErrorCode.FILE_NOT_PDF, status_code=415)

        stored = save_upload(upload.chunks())
        try:
            existing = ExamDocument.objects.filter(user=request.user, sha256=stored.sha256).first()
            if existing is not None:
                raise _duplicate_error(existing)
            try:
                with transaction.atomic():
                    # `page_count` e a validação de páginas ficam com o worker: não
                    # se interpreta PDF não confiável na thread web.
                    document = ExamDocument.objects.create(
                        user=request.user,
                        sha256=stored.sha256,
                        original_filename=upload.name[:255],
                        page_count=0,
                        upload_path=str(stored.path),
                    )
            except IntegrityError as exc:
                # Upload idêntico concorrente (unique user+sha256).
                existing = ExamDocument.objects.filter(
                    user=request.user, sha256=stored.sha256
                ).first()
                raise _duplicate_error(existing) from exc
        except BaseException:
            delete_upload(stored.path)
            raise

        _enqueue_extraction(document.pk)
        if settings.CELERY_TASK_ALWAYS_EAGER:
            # Modo inline: a extração já rodou nesta requisição. Devolve o estado de agora
            # (concluído, revisão, falha) para o frontend não precisar esperar o polling; se por
            # algum motivo ainda estiver em leitura, o polling de 5 s cobre.
            document = self.get_queryset().filter(pk=document.pk).first() or document
        return Response(ExamDocumentSerializer(document).data, status=status.HTTP_202_ACCEPTED)

    def perform_destroy(self, instance):
        delete_upload(instance.upload_path)
        instance.delete()

    def partial_update(self, request, *args, **kwargs):
        """Troca o laboratório do laudo (só deste laudo; a linha global não é renomeada)."""
        document = self.get_object()
        serializer = DocumentLaboratorySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        name = serializer.validated_data["laboratory_name"]

        try:
            with transaction.atomic():
                # Trava a linha: o worker grava o laboratório ao fim da extração.
                locked = ExamDocument.objects.select_for_update().get(
                    pk=document.pk, user=request.user
                )
                if locked.status in (DocumentStatus.UPLOADED, DocumentStatus.EXTRACTING):
                    raise CodedAPIError(ErrorCode.DOCUMENT_BUSY, status_code=409)
                laboratory = laboratory_for(name)
                clash = find_accession_clash(
                    request.user, laboratory, locked.accession_number, exclude=locked
                )
                if clash is not None:
                    raise _accession_conflict(clash)
                locked.laboratory = laboratory
                locked.save(update_fields=["laboratory", "updated_at"])
        except IntegrityError as exc:
            # Corrida: outro laudo ocupou o par (laboratório, pedido) entre a checagem e o save.
            clash = find_accession_clash(
                request.user, laboratory_for(name), document.accession_number, exclude=document
            )
            if clash is None:
                raise
            raise _accession_conflict(clash) from exc

        refreshed = self.get_queryset().get(pk=document.pk)
        return Response(ExamDocumentDetailSerializer(refreshed).data)

    @action(detail=True, methods=["get"], url_path="results")
    def results(self, request, pk=None):
        document = self.get_object()
        queryset = document.results.select_related("analyte").order_by("origin", "position")
        return Response(ExamResultSerializer(queryset, many=True).data)

    @action(detail=True, methods=["get"], url_path="runs")
    def runs(self, request, pk=None):
        document = self.get_object()
        return Response(ExtractionRunSerializer(document.runs.all(), many=True).data)

    @action(detail=True, methods=["post"], url_path="confirm")
    def confirm(self, request, pk=None):
        document = self.get_object()
        if document.status not in (DocumentStatus.NEEDS_REVIEW, DocumentStatus.COMPLETED):
            raise CodedAPIError(ErrorCode.DOCUMENT_NOT_READY, status_code=409)
        with transaction.atomic():
            document.results.exclude(review_status=ReviewStatus.USER_CORRECTED).update(
                review_status=ReviewStatus.CONFIRMED
            )
            # Blocos não lidos só saem da lista quando o usuário digita o resultado ou marca
            # como resolvido: confirmar o que FOI lido não pode esconder o que ficou de fora.
            document.warnings = [
                w for w in document.warnings if w.get("code") == WarningCode.BLOCK_UNREADABLE
            ]
            document.save(update_fields=["warnings", "updated_at"])
            refresh_document_status(document)
        return Response(ExamDocumentSerializer(self.get_queryset().get(pk=document.pk)).data)

    @action(detail=True, methods=["post"], url_path="dismiss-warning")
    def dismiss_warning(self, request, pk=None):
        """Marca um bloco não lido como resolvido: tira exatamente esse aviso do laudo.

        Corpo: `{code, page, title?}`. Só `block_unreadable` pode ser dispensado. Com `title`,
        só o aviso daquele bloco; sem, todos os da página (laudos antigos não têm título).
        Idempotente: se o aviso já não existe, responde 200 com o laudo como está.
        """
        document = self.get_object()
        serializer = DismissWarningSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if data["code"] != WarningCode.BLOCK_UNREADABLE:
            raise CodedAPIError(ErrorCode.WARNING_NOT_DISMISSIBLE)

        with transaction.atomic():
            # Trava a linha: o worker grava os avisos ao fim da extração.
            locked = ExamDocument.objects.select_for_update().get(pk=document.pk, user=request.user)
            if locked.status in (DocumentStatus.UPLOADED, DocumentStatus.EXTRACTING):
                raise CodedAPIError(ErrorCode.DOCUMENT_BUSY, status_code=409)
            kept = [w for w in locked.warnings if not _matches_warning(w, data)]
            if len(kept) != len(locked.warnings):
                locked.warnings = kept
                locked.save(update_fields=["warnings", "updated_at"])
                refresh_document_status(locked)
        return Response(ExamDocumentSerializer(self.get_queryset().get(pk=document.pk)).data)

    @action(detail=True, methods=["post"], url_path="reprocess")
    def reprocess(self, request, pk=None):
        document = self.get_object()
        # Reprocessar apaga e refaz os resultados: nunca com edição, confirmação ou linha digitada.
        edited = document.results.filter(
            Q(review_status__in=[ReviewStatus.USER_CORRECTED, ReviewStatus.CONFIRMED])
            | Q(origin=ResultOrigin.MANUAL)
        ).exists()
        if not document.page_text or edited:
            raise CodedAPIError(ErrorCode.DOCUMENT_NOT_REPROCESSABLE, status_code=409)
        # Transição atômica: só uma chamada concorrente vence.
        claimed = ExamDocument.objects.filter(
            pk=document.pk,
            user=request.user,
            status__in=[
                DocumentStatus.NEEDS_REVIEW,
                DocumentStatus.COMPLETED,
                DocumentStatus.FAILED,
            ],
        ).update(status=DocumentStatus.EXTRACTING)
        if not claimed:
            raise CodedAPIError(ErrorCode.DOCUMENT_NOT_REPROCESSABLE, status_code=409)
        _enqueue_extraction(document.pk, True)
        return Response(status=status.HTTP_202_ACCEPTED)


def _matches_warning(item: dict, target: dict) -> bool:
    params = item.get("params") or {}
    if item.get("code") != target["code"] or params.get("page") != target["page"]:
        return False
    return "title" not in target or params.get("title", "") == target["title"]


class ExamResultViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """Resultados do usuário. POST digita um resultado (`manual`); DELETE só apaga os manuais."""

    serializer_class = ExamResultSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = UnboundedCollectionPagination
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        queryset = ExamResult.objects.filter(user=self.request.user).select_related("analyte")
        params = self.request.query_params
        if (document := _int_param(params, "document")) is not None:
            queryset = queryset.filter(document_id=document)
        if params.get("analyte"):
            queryset = queryset.filter(analyte__slug=params["analyte"])
        for field, choices in (
            ("review_status", ReviewStatus),
            ("flag", ResultFlag),
            ("origin", ResultOrigin),
        ):
            if (value := _choice_param(params, field, choices)) is not None:
                queryset = queryset.filter(**{field: value})
        return queryset.order_by("-collected_at", "document_id", "position")

    def create(self, request, *args, **kwargs):
        """Digita um resultado que a leitura automática não pegou (origem `manual`)."""
        serializer = ManualResultSerializer(
            data=request.data, context=self.get_serializer_context()
        )
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        with transaction.atomic():
            # Trava o laudo: serializa criações concorrentes (duplicidade, posição) e o worker.
            document = ExamDocument.objects.select_for_update().get(
                pk=data["document"].pk, user=request.user
            )
            if document.status in (DocumentStatus.UPLOADED, DocumentStatus.EXTRACTING):
                raise CodedAPIError(ErrorCode.DOCUMENT_BUSY, status_code=409)

            collected_at = data.get("collected_at")
            if collected_at is None and document.collected_on is not None:
                collected_at = timezone.make_aware(
                    datetime.combine(document.collected_on, time.min)
                )

            key = duplicate_key(data["name_raw"], collected_at, data["value_raw"])
            for existing in document.results.only("id", "name_raw", "collected_at", "value_raw"):
                if (
                    duplicate_key(existing.name_raw, existing.collected_at, existing.value_raw)
                    == key
                ):
                    raise CodedAPIError(
                        ErrorCode.RESULT_DUPLICATE, {"result_id": existing.pk}, status_code=409
                    )

            last = document.results.aggregate(position=Max("position"), page=Max("page"))
            profile = Profile.objects.filter(user=request.user).first()
            explicit = data.get("analyte")
            result = build_manual_result(
                user=request.user,
                document=document,
                name_raw=data["name_raw"],
                value_raw=data["value_raw"],
                unit_raw=data["unit_raw"],
                reference_raw=data["reference_raw"],
                collected_at=collected_at,
                notes=data["notes"],
                analyte=explicit,
                resolver=AnalyteResolver(user=request.user),
                sex=(profile.sex or None) if profile else None,
                birth_date=profile.birth_date if profile else None,
                page=last["page"] or 1,
                position=0 if last["position"] is None else last["position"] + 1,
            )
            _check_decimal_bounds(result)
            result.save()
            if explicit is not None:
                learn_alias(request.user, result.name_raw, explicit)
            refresh_document_status(document)
        return Response(ExamResultSerializer(result).data, status=status.HTTP_201_CREATED)

    def destroy(self, request, *args, **kwargs):
        """Desfaz um resultado digitado. Os lidos do laudo não se apagam (reprocesse o laudo)."""
        result = self.get_object()
        if result.origin != ResultOrigin.MANUAL:
            raise CodedAPIError(ErrorCode.RESULT_NOT_MANUAL, status_code=403)
        with transaction.atomic():
            document = ExamDocument.objects.select_for_update().get(
                pk=result.document_id, user=request.user
            )
            result.delete()
            refresh_document_status(document)
        return Response(status=status.HTTP_204_NO_CONTENT)

    def perform_update(self, serializer):
        before = _editable_snapshot(serializer.instance)
        with transaction.atomic():
            result: ExamResult = serializer.save()
            changed = _editable_snapshot(result) != before
            if changed:
                profile = Profile.objects.filter(user=self.request.user).first()
                resolver = AnalyteResolver(user=self.request.user)
                recompute_result(
                    result,
                    sex=(profile.sex or None) if profile else None,
                    birth_date=profile.birth_date if profile else None,
                    resolver=resolver,
                )
                result.review_status = ReviewStatus.USER_CORRECTED
                result.warnings = []
                if result.analyte_id != before[0] and result.analyte is not None:
                    learn_alias(self.request.user, result.name_raw, result.analyte)
                result.save()
            refresh_document_status(result.document)


def _check_decimal_bounds(result: ExamResult) -> None:
    """Valores que não cabem nas colunas numéricas viram erro de campo, nunca 500."""
    if any(
        value is not None and abs(value) >= MAX_ABS_DECIMAL
        for value in (result.value_numeric, result.value_canonical)
    ):
        raise serializers.ValidationError({"value_raw": [ErrorCode.RESULT_VALUE_INVALID.value]})
    if any(
        value is not None and abs(value) >= MAX_ABS_DECIMAL
        for value in (result.ref_low, result.ref_high)
    ):
        raise serializers.ValidationError(
            {"reference_raw": [ErrorCode.RESULT_REFERENCE_INVALID.value]}
        )


def _editable_snapshot(result: ExamResult) -> tuple:
    return (
        result.analyte_id,
        result.value_raw,
        result.unit_raw,
        result.reference_raw,
        result.collected_at,
    )
