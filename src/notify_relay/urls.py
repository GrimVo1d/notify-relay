from django.contrib import admin
from django.urls import include, path
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from apps.core.health import live, ready

urlpatterns = [
    path("health/live", live, name="health-live"),
    path("health/ready", ready, name="health-ready"),
    path("admin/", admin.site.urls),
    path("api/v1/auth/token/", TokenObtainPairView.as_view(), name="auth_token_obtain"),
    path("api/v1/auth/token/refresh/", TokenRefreshView.as_view(), name="auth_token_refresh"),
    path("api/v1/", include("apps.messages_api.urls")),
    path("api/v1/", include("apps.templating.urls")),
    path("api/v1/", include("apps.core.urls")),
]
