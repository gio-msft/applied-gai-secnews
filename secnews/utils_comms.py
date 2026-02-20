import logging
import datetime

from pathlib import Path
from email.mime.text import MIMEText

logger = logging.getLogger("AIRT-GAI-SecNews")


def _format_record_markdown(record: dict) -> str:
    """Format a single record for markdown."""
    content = (
        f"{record['emoji']} **{record['title']}** [source]({record['url']}) "
        f"#{record['tag']} \n"
    )
    authors = record.get("authors", [])
    affiliations = record.get("affiliations", [])
    if authors or affiliations:
        parts = []
        if authors:
            parts.append(", ".join(authors))
        if affiliations:
            parts.append("(" + ", ".join(affiliations) + ")")
        content += f"\n *{' '.join(parts)}*"
    content += f"\n\n {record['one_liner']}"
    for point in record["points"]:
        content += f"\n - {point}"
    return content + "\n\n<br>\n\n"


def _format_record_html(record: dict) -> str:
    """Format a single record as Outlook-friendly HTML with inline styles."""
    points_html = "".join(
        f'<li style="margin-bottom:4px;">{p}</li>' for p in record["points"]
    )
    authors = record.get("authors", [])
    affiliations = record.get("affiliations", [])
    byline = ""
    if authors or affiliations:
        parts = []
        if authors:
            parts.append(", ".join(authors))
        if affiliations:
            parts.append("(" + ", ".join(affiliations) + ")")
        byline = (
            f'<p style="margin:0 0 4px 0;color:#888;font-size:0.85em;">'
            f'{" ".join(parts)}</p>'
        )
    return (
        f'<div style="margin-bottom:24px;">'
        f'<p style="margin:0 0 6px 0;">'
        f'{record["emoji"]} '
        f'<b><a href="{record["url"]}" style="color:#1a0dab;text-decoration:none;">'
        f'{record["title"]}</a></b> '
        f'<span style="color:#666;font-size:0.85em;">#{record["tag"]}</span>'
        f'</p>'
        f'{byline}'
        f'<p style="margin:0 0 6px 0;color:#333;">{record["one_liner"]}</p>'
        f'<ul style="margin:0 0 0 18px;padding:0;color:#444;">{points_html}</ul>'
        f'<hr style="border:none;border-top:1px solid #e0e0e0;margin-top:16px;"/>'
        f'</div>'
    )


def share_results(
    pull_window: str,
    paper_db,
    summaries_path: str,
    include_all: bool = False,
) -> bool:
    """Prepare summarized results and write markdown + eml files."""
    try:
        records = paper_db.find(published_gte=pull_window, summarized=True)
        if not records:
            return False

        if not include_all:
            before = len(records)
            records = [r for r in records if r.get("relevant") is True]
            filtered = before - len(records)
            if filtered:
                logger.info(f"Filtered out {filtered} irrelevant papers.")

        if not records:
            logger.info("No relevant papers to share after filtering.")
            return False

        logger.info(f"Found {len(records)} records to share.")
        for record in records:
            logger.debug(f"{record['published']} {record['title']}")

        markdown = "".join(_format_record_markdown(r) for r in records)
        html = "".join(_format_record_html(r) for r in records)

        _create_markdown_file(summaries_path, markdown)
        _create_eml_file(summaries_path, html)
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


def _create_eml_file(summaries_path: str, html_content: str) -> None:
    """Create an .eml file that opens directly in Outlook with formatting."""
    try:
        summaries_dir = Path(summaries_path)
        summaries_dir.mkdir(exist_ok=True)

        today = datetime.datetime.now().strftime("%Y-%m-%d")
        filename = f"{today}.eml"

        body_html = (
            '<html><head><meta charset="utf-8"></head>'
            '<body style="font-family:Calibri,Arial,sans-serif;'
            'max-width:800px;padding:16px;font-size:14px;">'
            f'{html_content}'
            '</body></html>'
        )

        msg = MIMEText(body_html, "html", "utf-8")
        msg["Subject"] = f"[{today}] AIRT Gen AI Security News"
        msg["X-Unsent"] = "1"

        eml_file = summaries_dir / filename
        eml_file.write_text(msg.as_string())
        logger.info(f"EML file created: {eml_file}")

    except Exception as e:
        logger.error(f"Failed to create EML file: {e}")
