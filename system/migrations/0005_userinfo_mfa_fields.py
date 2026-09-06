from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('system', '0004_operationlog_idx_oplog_created_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='userinfo',
            name='mfa_level',
            field=models.IntegerField(choices=[(0, 'Disabled'), (1, 'Enabled')], default=0,
                                      verbose_name='MFA level'),
        ),
        migrations.AddField(
            model_name='userinfo',
            name='otp_secret_key',
            field=models.CharField(blank=True, default='', max_length=64, verbose_name='OTP secret key'),
        ),
    ]
