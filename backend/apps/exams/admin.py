from django.contrib import admin

from .models import (
    Analyte,
    AnalyteAlias,
    ExamDocument,
    ExamResult,
    ExtractionRun,
    Laboratory,
    Profile,
    UnitConversion,
)


class ReadOnlyAddAdmin(admin.ModelAdmin):
    """Laudos e resultados nascem do pipeline; o admin só consulta/edita."""

    def has_add_permission(self, request):
        return False


@admin.register(ExamDocument)
class ExamDocumentAdmin(ReadOnlyAddAdmin):
    # `page_text` é dado de saúde (texto integral do laudo): fora do admin.
    exclude = ("page_text",)


@admin.register(ExamResult)
class ExamResultAdmin(ReadOnlyAddAdmin):
    # `raw` e `source_line` repetem o texto impresso do laudo.
    exclude = ("raw", "source_line")


admin.site.register(Profile)
admin.site.register(Laboratory)
admin.site.register(ExtractionRun)
admin.site.register(Analyte)
admin.site.register(AnalyteAlias)
admin.site.register(UnitConversion)
