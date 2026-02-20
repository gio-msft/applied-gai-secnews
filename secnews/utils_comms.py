import os
import smtplib
import logging
import datetime

from pathlib import Path
from pymongo import MongoClient
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

logger = logging.getLogger("AIRT-GAI-SecNews")


def send_mail(subject: str, content: dict, send_to: str) -> None:
    """Send mail out to users."""
    sender_email = os.environ.get("SENDER_EMAIL")
    from_email = os.environ.get("FROM_EMAIL")
    app_password = os.environ.get("APP_PASSWORD")

    if not all([sender_email, from_email, app_password]):
        logger.error("Missing email configuration environment variables")
        return

    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(sender_email, app_password)

            message = MIMEMultipart("alternative")
            message["Subject"] = subject
            message["From"] = from_email
            message["To"] = send_to
            message.attach(MIMEText(content["plain"], "plain"))
            message.attach(MIMEText(content["html"], "html"))

            server.sendmail(from_email, send_to, message.as_string())
            logger.info(f"Email sent successfully to {send_to}")
    except Exception as e:
        logger.error(f"Failed to send email to {send_to}: {e}")


def _format_record_plain(record: dict) -> str:
    """Format a single record for plain text email."""
    content = f"{record['emoji']} {record['title']}\n {record['url']} - {record['tag']}\n - {record['one_liner']}\n"
    for point in record["points"]:
        content += f"- {point}\n"
    return content + "\n\n"


def _format_record_html(record: dict) -> str:
    """Format a single record for HTML email."""
    content = (
        f"<b>{record['emoji']} {record['title']}</b> "
        f"(<a href='{record['url']}' target='_blank'>{record['url']}</a>)<br> "
        f"{record['tag']} - {record['one_liner']}<br>"
    )
    for point in record["points"]:
        content += f"<li>{point}</li>"
    return content + "<br>"


def _format_record_markdown(record: dict) -> str:
    """Format a single record for markdown."""
    content = (
        f"{record['emoji']} **{record['title']}** [source]({record['url']}) "
        f"#{record['tag']} \n\n {record['one_liner']}"
    )
    for point in record["points"]:
        content += f"\n - {point}"
    return content + "\n\n<br>\n\n"


def share_results(
    pull_window: str,
    research_db: MongoClient,
    email_list: list,
    today: str,
    summaries_path: str,
) -> bool:
    """Prepare any result not yet shared and format."""
    query = {
        "$and": [
            {"published": {"$gte": pull_window}},
            {"summarized": True},
            # {"shared": False},
        ]
    }

    try:
        records = list(research_db.find(query))
        if not records:
            return False

        logger.info(f"Found {len(records)} records to share.")
        for record in records:
            logger.debug(f"{record['published']} {record['title']}")

        # Build content using helper functions
        content = {
            "plain": "".join(_format_record_plain(record) for record in records),
            "html": "".join(_format_record_html(record) for record in records),
            "markdown": "".join(_format_record_markdown(record) for record in records),
        }

        # logger.debug(content)

        # Send emails if there's content
        if content["plain"]:
            subject = f"[{today[:10]}] AIRT Gen AI Security News"
            for user in email_list:
                send_mail(subject, content, user)

        # Mark records as shared
        record_ids = [record["id"] for record in records]
        research_db.update_many({"id": {"$in": record_ids}}, {"$set": {"shared": True}})

        # Create markdown file
        _create_markdown_file(summaries_path, content["markdown"])

        return True

    except Exception as e:
        logger.error(f"Error in share_results: {e}")
        return False


def _create_markdown_file(summaries_path: str, markdown_content: str) -> None:
    """Create a markdown file with the summary content."""
    try:
        summaries_dir = Path(summaries_path)
        summaries_dir.mkdir(exist_ok=True)

        filename = f"{datetime.datetime.now().strftime('%Y-%m-%d')}.md"
        markdown_file = summaries_dir / filename

        markdown_file.write_text(markdown_content)
        logger.info(f"Markdown file created: {markdown_file}")

    except Exception as e:
        logger.error(f"Failed to create markdown file: {e}")
