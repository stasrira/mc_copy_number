import os
import yagmail


def send_email(emails_to, subject, message, email_from=None, attachment_path=None, smtp_server=None, smtp_server_port=None):
    """Send email using yagmail with SMTP settings from environment variables.

    :param emails_to: List of recipient email addresses or comma-separated string
    :param subject: Email subject line
    :param message: Email body (can be plain text or HTML)
    :param email_from: Sender email address (optional)
    :param attachment_path: Path to file to attach (optional)
    :param smtp_server: SMTP server hostname (optional, defaults to SRD_SMTP_SERVER env var)
    :param smtp_server_port: SMTP server port (optional, defaults to SRD_SMTP_SERVER_PORT env var or 25)
    """
    if not smtp_server:
        smtp_server = os.environ.get('SRD_SMTP_SERVER')
    if not smtp_server_port:
        port_str = os.environ.get('SRD_SMTP_SERVER_PORT')
        if port_str and port_str.isnumeric():
            smtp_server_port = int(port_str)
        else:
            smtp_server_port = 25

    yag = yagmail.SMTP(
        email_from,
        host=smtp_server,
        smtp_skip_login=True,
        smtp_ssl=False,
        soft_email_validation=False,
        port=smtp_server_port
    )
    yag.send(
        to=emails_to,
        subject=subject,
        contents=message,
        attachments=attachment_path,
    )
