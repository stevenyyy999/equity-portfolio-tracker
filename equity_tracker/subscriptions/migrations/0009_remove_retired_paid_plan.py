from django.db import migrations, models


def remove_retired_paid_plan(apps, schema_editor):
    Subscription = apps.get_model("subscriptions", "Subscription")
    StripeCustomer = apps.get_model("subscriptions", "StripeCustomer")
    Profile = apps.get_model("users", "Profile")

    removed_plan_name = "".join(("excl", "usive"))
    removed_plan = Subscription.objects.filter(name=removed_plan_name).first()
    if removed_plan is None:
        return

    replacement_plan = Subscription.objects.filter(name="premium").first()
    StripeCustomer.objects.filter(subscription=removed_plan).update(
        subscription=replacement_plan
    )
    Profile.objects.filter(subscription=removed_plan).update(
        subscription=replacement_plan
    )
    removed_plan.delete()


class Migration(migrations.Migration):
    dependencies = [
        (
            "subscriptions",
            "0008_alter_stripecustomer_id_alter_stripetransaction_id_and_more",
        ),
        ("users", "0004_alter_profile_id"),
    ]

    operations = [
        migrations.RunPython(remove_retired_paid_plan, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="subscription",
            name="name",
            field=models.CharField(
                choices=[
                    ("free", "Free"),
                    ("premium", "Premium"),
                ],
                max_length=10,
                unique=True,
            ),
        ),
    ]
