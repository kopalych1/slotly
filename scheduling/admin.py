from django.contrib import admin

from scheduling.services import bulk_fill_slots

from .models import Exam, ExamDay, Offer, Slot, SwapLog


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

    def save_model(self, request, obj, form, change):

        super().save_model(request, obj, form, change)
        if not change:
            bulk_fill_slots(obj)


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


@admin.register(SwapLog)
class SwapLogAdmin(admin.ModelAdmin):
    list_display = ('exam_name', 'sender_name', 'receiver_name', 'exchange_datetime')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
