import graphene
from django.db.models import Count, Q
from graphene_django import DjangoObjectType

from .history import build_analyte_history, build_overview
from .models import CURRENT_ORIGINS, Analyte, ExamDocument, ReviewStatus

MAX_DOCUMENTS = 500


class ExamDocumentType(DjangoObjectType):
    result_count = graphene.Int()
    review_count = graphene.Int()
    laboratory_name = graphene.String()

    class Meta:
        model = ExamDocument
        fields = (
            "id",
            "original_filename",
            "status",
            "collected_on",
            "accession_number",
            "ordering_physician",
            "created_at",
        )

    def resolve_laboratory_name(self, info):
        return self.laboratory.name if self.laboratory else ""


class AnalyteOverviewType(graphene.ObjectType):
    slug = graphene.String()
    name = graphene.String()
    category = graphene.String()
    unit = graphene.String()
    kind = graphene.String()
    count = graphene.Int()
    latest_value = graphene.Float()
    latest_text = graphene.String()
    latest_at = graphene.String()
    latest_flag = graphene.String()
    latest_interpretation = graphene.String()
    delta = graphene.Float()
    delta_pct = graphene.Float()
    sparkline = graphene.List(graphene.Float)


class AnalyteInfoType(graphene.ObjectType):
    id = graphene.Int()
    slug = graphene.String()
    name = graphene.String()
    category = graphene.String()
    unit = graphene.String()
    kind = graphene.String()


class HistoryPointType(graphene.ObjectType):
    id = graphene.Int()
    document_id = graphene.Int()
    laboratory = graphene.String()
    origin = graphene.String()
    collected_at = graphene.String()
    value = graphene.Float()
    value_raw = graphene.String()
    value_text = graphene.String()
    comparator = graphene.String()
    unit = graphene.String()
    ref_low = graphene.Float()
    ref_high = graphene.Float()
    reference_raw = graphene.String()
    flag = graphene.String()
    interpretation = graphene.String()
    review_status = graphene.String()


class AnalyteHistoryType(graphene.ObjectType):
    analyte = graphene.Field(AnalyteInfoType)
    chartable = graphene.Boolean()
    points = graphene.List(HistoryPointType)


def _documents_with_counts(user):
    """Contagens por anotação (uma query), sem N+1 nos resolvers de campo."""
    current = Q(results__origin__in=CURRENT_ORIGINS)
    return (
        ExamDocument.objects.filter(user=user)
        .select_related("laboratory")
        .annotate(
            result_count=Count("results", filter=current),
            review_count=Count(
                "results", filter=current & Q(results__review_status=ReviewStatus.NEEDS_REVIEW)
            ),
        )
    )


def _authenticated(info):
    user = info.context.user
    return user if user and user.is_authenticated else None


class Query(graphene.ObjectType):
    exam_documents = graphene.List(ExamDocumentType, limit=graphene.Int(default_value=100))
    exam_document = graphene.Field(ExamDocumentType, id=graphene.Int(required=True))
    analytes_overview = graphene.List(AnalyteOverviewType)
    analyte_history = graphene.Field(AnalyteHistoryType, slug=graphene.String(required=True))

    def resolve_exam_documents(self, info, limit):
        user = _authenticated(info)
        if user is None:
            return []
        limit = max(1, min(limit, MAX_DOCUMENTS))
        return _documents_with_counts(user).order_by("-collected_on", "-created_at")[:limit]

    def resolve_exam_document(self, info, id):
        user = _authenticated(info)
        if user is None:
            return None
        return _documents_with_counts(user).filter(pk=id).first()

    def resolve_analytes_overview(self, info):
        user = _authenticated(info)
        return [AnalyteOverviewType(**row) for row in build_overview(user)] if user else []

    def resolve_analyte_history(self, info, slug):
        user = _authenticated(info)
        analyte = Analyte.objects.filter(slug=slug).first()
        if user is None or analyte is None:
            return None
        data = build_analyte_history(user, analyte)
        return AnalyteHistoryType(
            analyte=AnalyteInfoType(**data["analyte"]),
            chartable=data["chartable"],
            points=[HistoryPointType(**p) for p in data["points"]],
        )
