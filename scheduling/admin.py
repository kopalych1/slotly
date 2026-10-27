from django.contrib import admin

from .models import Exam, ExamDay, Offer, Slot


@admin.register(Exam)
class ExamAdmin(admin.ModelAdmin):
    list_display = (
        'name',
        'owner',
        'slots_change_deadline',
    )


@admin.register(ExamDay)
class ExamDayAdmin(admin.ModelAdmin):
    list_display = ('exam', 'start', 'end')


@admin.register(Slot)
class SlotAdmin(admin.ModelAdmin):
    list_display = ('exam', 'start', 'owner')

    list_filter = ('exam', 'exam_day')
    readonly_fields = ('exam',)


@admin.register(Offer)
class OfferAdmin(admin.ModelAdmin):
    list_display = (
        'offeror',
        'offered_slot',
        'wanted_slot',
        'status',
        'expires_at',
    )
    list_filter = ('status',)
