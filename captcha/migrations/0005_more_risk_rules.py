from django.db import migrations

NEW_RULES = (
    ("freq_ip_fp_spread", "Many fingerprints per IP", 25, 105),
    ("freq_fp_ip_spread", "Many IPs per fingerprint", 30, 106),
    ("challenge_abandon", "Challenge / verify ratio anomaly", 20, 107),
    ("type_fixation", "Fixed challenge type", 15, 108),
    ("audio_abuse", "Audio challenge abuse", 20, 109),
    ("ticket_velocity", "Ticket consumption velocity", 20, 111),
)


def seed_more_rules(apps, schema_editor):
    rule_model = apps.get_model("captcha", "CaptchaRule")
    existing = set(rule_model.objects.values_list("code", flat=True))
    rule_model.objects.bulk_create(
        [
            rule_model(code=code, name=name, score=score, priority=priority, enabled=True, scope="", params={})
            for code, name, score, priority in NEW_RULES
            if code not in existing
        ],
        ignore_conflicts=True,
    )


class Migration(migrations.Migration):
    dependencies = [
        ("captcha", "0004_delete_captchastore"),
    ]

    operations = [
        migrations.RunPython(seed_more_rules, migrations.RunPython.noop),
    ]
