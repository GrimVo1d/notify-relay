from django.contrib import admin
from django.urls import include, path
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/auth/token/", TokenObtainPairView.as_view(), name="auth_token_obtain"),
    path("api/v1/auth/token/refresh/", TokenRefreshView.as_view(), name="auth_token_refresh"),
    path("api/v1/", include("apps.messages_api.urls")),
]
