from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="ExchangeRate",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("to_currency", models.CharField(max_length=3)),
                ("rate", models.FloatField()),
                ("date_updated", models.DateTimeField(auto_now=True)),
            ],
        ),
    ]
