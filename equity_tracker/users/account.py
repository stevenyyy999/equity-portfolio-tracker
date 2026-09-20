from allauth.account.models import EmailAddress
from allauth.account.signals import email_confirmed
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.dispatch import receiver


def ensure_primary_email_record(user):
    """Ensure the user's current email has a primary EmailAddress record."""
    email = (user.email or "").strip()
    if not email:
        return None

    email_address, _ = EmailAddress.objects.get_or_create(
        user=user,
        email=email,
        defaults={
            "primary": True,
            "verified": True,
        },
    )

    EmailAddress.objects.filter(user=user, email=email).update(primary=True)
    return email_address


def get_pending_email(user):
    """Return the newest unverified email change request for a user."""
    return (
        EmailAddress.objects.filter(user=user, verified=False)
        .exclude(email=user.email)
        .order_by("-pk")
        .first()
    )


# Takes in an existing email address and makes it the user's primary login email
def promote_email_record(user, email_record):
    """Takes in existing email and makes it the user's primary login email"""
    EmailAddress.objects.filter(user=user).update(primary=False)
    EmailAddress.objects.filter(pk=email_record.pk).update(
        primary=True,
        verified=True,
    )

    user.email = email_record.email
    user.save(update_fields=["email"])


# fire allauth confirmation email via the verification link
@receiver(email_confirmed)
def handle_email_confirmed(sender, request, email_address, **kwargs):
    """Sends allauth confirmation email via the verification link"""
    promote_email_record(email_address.user, email_address)


# Depending on the email form, we change up the email
def process_email_change_request(request):
    """Handle save/delete actions for email records on the profile page."""
    success_message = ""
    error_message = ""
    pending_email = get_pending_email(request.user)

    action = request.POST.get("action")
    raw_email = (request.POST.get("email") or "").strip().lower()
    delete_email = (request.POST.get("delete_email") or "").strip().lower()

    # Delete an email via the delete button
    if action == "delete_record":
        # Find the email to delete
        email_record = EmailAddress.objects.filter(
            user=request.user, email__iexact=delete_email
        ).first()

        # Return error if not found or primary, otherwise proceed with deletion
        if email_record is None:
            error_message = "That email record could not be found."
        elif (
            email_record.primary
            or delete_email == (request.user.email or "").strip().lower()
        ):
            error_message = "You cannot delete your current primary email."
        else:
            email_record.delete()
            success_message = "Email record deleted successfully."

    # No email provided, but we're to saved the email. Return error.
    if not error_message and action == "save" and not raw_email:
        error_message = "Please enter a new email address."

    # Email provided, and we're told to save
    if not error_message and action == "save":
        # Validate, if fail, return error message
        try:
            validate_email(raw_email)
        except ValidationError:
            error_message = "Please enter a valid email address."

    # All good so far but the email is the same as what we got, if it is empty, this will run but won't crash
    if not error_message and raw_email == (request.user.email or "").strip().lower():
        error_message = "That email is already your current login email."

    # All is good so far, but now ensures email is unique
    email_in_use = (
        EmailAddress.objects.filter(email__iexact=raw_email)
        .exclude(user=request.user)
        .exists()
    )
    if not error_message and email_in_use:
        error_message = "That email is already associated with another account."

    # User has an email pending. Verify before use.
    if (
        not error_message
        and pending_email
        and raw_email != pending_email.email.lower()
        and action == "save"
    ):
        error_message = "You already have a pending email change. Verify or cancel it before entering another email."

    # Save new email
    if not error_message and action == "save":
        existing_email_record = EmailAddress.objects.filter(
            user=request.user, email__iexact=raw_email
        ).first()

        if existing_email_record and existing_email_record.verified:
            promote_email_record(request.user, existing_email_record)
            success_message = (
                "That verified email was reused and is now your login email."
            )
        else:
            email_record, _ = EmailAddress.objects.update_or_create(
                user=request.user,
                email=raw_email,
                defaults={
                    "primary": False,
                    "verified": False,
                },
            )

            # send email
            email_record.send_confirmation(request)
            success_message = "New email saved. It is pending verification."

    return success_message, error_message


# Gathers all the email-related data the profile template needs and packages it into
# one dictionary for the website
def build_email_change_context(user, success_message="", error_message=""):
    """Packages all the email-related data the profile template needs into a dictionary"""
    ensure_primary_email_record(user)
    pending_email = get_pending_email(user)
    email_addresses = EmailAddress.objects.filter(user=user).order_by(
        "-primary", "email"
    )
    return {
        "current_email": user.email,
        "pending_email": pending_email.email if pending_email else "",
        "has_pending_email": bool(pending_email),
        "email_addresses": email_addresses,
        "email_success_message": success_message,
        "email_error_message": error_message,
    }
