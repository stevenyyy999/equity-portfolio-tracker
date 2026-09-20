"""
URL configuration for portfolio project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from core.views import dashboard, features, landing
from django.contrib import admin
from django.urls import include, path
from taxCalculator.views import calculate_capital_gains_taxes, export_cgt_csv
from users.views import profile_page

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/profile/", profile_page, name="account-profile"),
    path("users/", include("users.urls")),
    path("stripe/", include("subscriptions.urls")),
    path("accounts/", include("allauth.urls")),
    path("", landing, name="landing"),
    path("dashboard/", dashboard, name="dashboard"),
    path("transactions/", include("transactions.urls")),
    path("portfolios/", include("portfolios.urls")),
    path("features/", features, name="features"),
    path("cgt-calculator/", calculate_capital_gains_taxes, name="cgt-calculator"),
    path("cgt-calculator/export-cgt-csv/", export_cgt_csv, name="export_cgt_csv"),
]
