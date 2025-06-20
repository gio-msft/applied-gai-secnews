import os
import smtplib
import logging
import datetime

from pathlib import Path
from pymongo import MongoClient
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

logger = logging.getLogger("AIRT-GAI-SecNews")


def send_mail(subject, content, send_to) -> None:
    sender_email = os.environ.get("SENDER_EMAIL")
    from_email = os.environ.get("FROM_EMAIL")
    app_password = os.environ.get("APP_PASSWORD")

    """Send mail out to users."""
    server = smtplib.SMTP("smtp.gmail.com", 587)
    server.starttls()
    server.login(sender_email, app_password)
    message = MIMEMultipart("alternative")
    message["subject"] = subject
    message["From"] = from_email
    message["To"] = send_to
    message.attach(MIMEText(content["plain"], "plain"))
    message.attach(MIMEText(content["html"], "html"))
    server.sendmail(message["From"], message["To"], message.as_string())
    server.quit()


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
            {"shared": False},
        ]
    }
    tmp = research_db.find(query)
    if not tmp:
        return False
    tmp = list(tmp)
    for record in tmp:
        logger.debug("%s %s" % (record["published"], record["title"]))

    content = {"plain": "", "html": "", "markdown": ""}
    for record in tmp:
        content["plain"] += "%s %s\n %s - %s\n - %s\n" % (
            record["emoji"],
            record["title"],
            record["url"],
            record["tag"],
            record["one_liner"],
        )
        content[
            "html"
        ] += "<b>%s %s</b> (<a href='%s' target='_blank'>%s</a>)<br> %s - %s<br>" % (
            record["emoji"],
            record["title"],
            record["url"],
            record["url"],
            record["tag"],
            record["one_liner"],
        )
        content["markdown"] += "%s **%s** [source](%s) #%s \n\n %s" % (
            record["emoji"],
            record["title"],
            record["url"],
            record["tag"],
            record["one_liner"],
        )
        for point in record["points"]:
            content["plain"] += "- %s\n" % (point)
            content["html"] += "<li>%s</li>" % (point)
            content["markdown"] += "\n - %s" % (point)
        content["plain"] += "\n\n"
        content["html"] += "<br>"
        content["markdown"] += "\n\n<br>\n\n"

    logger.debug(content)
    if len(content["plain"]) > 0:
        for user in email_list:
            subject = "[%s] AIRT Gen AI Security News" % (today[:10])
            send_mail(subject, content, user)

    for record in tmp:
        query = {"id": record["id"]}
        setter = {"$set": {"shared": True}}
        research_db.update_one(query, setter)

    # Create a markdown file for sharing in SUMMARIES_PATH
    # The filename should be YYYY-MM-DD.md
    os.makedirs(summaries_path, exist_ok=True)
    markdown_file = Path(
        summaries_path + datetime.datetime.now().strftime("%Y-%m-%d") + ".md"
    )
    with open(markdown_file, "w") as f:
        f.write(content["markdown"])
    logger.info("Markdown file created: %s" % markdown_file)

    return True
