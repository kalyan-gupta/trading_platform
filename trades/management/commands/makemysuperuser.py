from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth.models import User

class Command(BaseCommand):
    help = 'Promote an existing user to superuser status'

    def add_arguments(self, parser):
        parser.add_argument('username', type=str, help='The username of the account to promote to superuser')

    def handle(self, *args, **options):
        username = options['username']
        
        try:
            user = User.objects.get(username=username)
            
            if user.is_superuser:
                self.stdout.write(self.style.WARNING(f'User "{username}" is already a superuser.'))
            else:
                user.is_superuser = True
                user.is_staff = True
                user.save()
                self.stdout.write(self.style.SUCCESS(f'Successfully promoted user "{username}" to superuser.'))
                
        except User.DoesNotExist:
            raise CommandError(f'User "{username}" does not exist.')
