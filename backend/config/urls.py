from django.conf import settings
from django.contrib import admin
from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView

from apps.exams.graphql_view import JWTGraphQLView
from apps.exams.views import (
    AnalyteViewSet,
    CurrentUserAPIView,
    ExamDocumentViewSet,
    ExamResultViewSet,
    HealthCheckAPIView,
    LaboratoryListAPIView,
    OverviewAPIView,
    ProfileAPIView,
    RegisterAPIView,
    ThrottledTokenObtainPairView,
)

router = DefaultRouter()
router.register(r"exam-documents", ExamDocumentViewSet, basename="exam-document")
router.register(r"exam-results", ExamResultViewSet, basename="exam-result")
router.register(r"analytes", AnalyteViewSet, basename="analyte")

urlpatterns = [
    path("admin/", admin.site.urls),
    path("health/", HealthCheckAPIView.as_view(), name="health"),
    path("api/v1/auth/me/", CurrentUserAPIView.as_view(), name="current-user"),
    path("api/v1/auth/register/", RegisterAPIView.as_view(), name="register"),
    path("api/v1/auth/token/", ThrottledTokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("api/v1/auth/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("api/v1/laboratories/", LaboratoryListAPIView.as_view(), name="laboratory-list"),
    path("api/v1/profile/", ProfileAPIView.as_view(), name="profile"),
    path("api/v1/overview/", OverviewAPIView.as_view(), name="overview"),
    path("api/v1/", include(router.urls)),
    path("graphql/", JWTGraphQLView.as_view(graphiql=settings.DEBUG)),
]
