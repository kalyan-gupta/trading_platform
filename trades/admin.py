from django.contrib import admin
from .models import UserNeoCredentials, SessionActivity


@admin.register(UserNeoCredentials)
class UserNeoCredentialsAdmin(admin.ModelAdmin):
    list_display = ('user', 'account_name', 'ucc', 'is_active', 'updated_at')
    search_fields = ('user__username', 'ucc', 'account_name')
    list_filter = ('is_active',)
    readonly_fields = ('created_at', 'updated_at', 'last_used')


@admin.register(SessionActivity)
class SessionActivityAdmin(admin.ModelAdmin):
    list_display = ('user', 'session_key', 'ip_address', 'last_activity')
    search_fields = ('user__username', 'session_key', 'ip_address')
    readonly_fields = ('last_activity',)


from .models import PlatformSettings, SMTPSettings

@admin.register(PlatformSettings)
class PlatformSettingsAdmin(admin.ModelAdmin):
    list_display = ('id', 'session_timeout_enabled', 'session_timeout_seconds', 'sdk_timeout_enabled', 'sdk_timeout_seconds')

@admin.register(SMTPSettings)
class SMTPSettingsAdmin(admin.ModelAdmin):
    list_display = ('id', 'host', 'port', 'enable_password_reset', 'enable_registration_otp')
