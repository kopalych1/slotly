"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.1/topics/http/urls/
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

from django.contrib import admin
from django.urls import include, path

import scheduling.views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/', include('django.contrib.auth.urls')),
    path('', scheduling.views.index, name='home'),
    path('exams/<int:pk>/', scheduling.views.exam_detail, name='exam_detail'),
    path(
        'slots/<int:slot_id>/take/', scheduling.views.take_slot_view, name='slot_take'
    ),
    path(
        'slots/<int:slot_id>/release/',
        scheduling.views.release_slot_view,
        name='slot_release',
    ),
    path(
        'slots/<int:slot_id>/offer/',
        scheduling.views.create_offer_view,
        name='offer_create',
    ),
    path(
        'offers/<int:offer_id>/accept/',
        scheduling.views.accept_offer_view,
        name='offer_accept',
    ),
    path(
        'offers/<int:offer_id>/decline/',
        scheduling.views.decline_offer_view,
        name='offer_decline',
    ),
]
