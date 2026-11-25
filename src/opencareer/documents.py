"""Turn a Markdown resume or cover letter into a clean, printable page or PDF."""
import html
from pathlib import Path

import markdown as md
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

TEMPLATE_DIR = Path(__file__).parent / "templates"


def render_markdown(text: str) -> Markup:
    """Markdown -> HTML, with any raw HTML in the text escaped first (never executed)."""
    return Markup(md.markdown(html.escape(text or "", quote=False)))


_env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=select_autoescape(["html"]))
_env.filters["markdown"] = render_markdown


def document_html(text: str, doc: str, company: str) -> str:
    """The same page as the dashboard's PDF view, without the on-screen hint bar."""
    job = type("Job", (), {"company": company})()
    return _env.get_template("print.html").render(text=text, doc=doc, job=job, pdf=True)


def write_pdf(page, html_text: str, path: Path) -> Path:
    """Print HTML to a Letter-size PDF using an open Playwright page."""
    page.set_content(html_text, wait_until="load")
    page.pdf(path=str(path), format="Letter", print_background=True,
             margin={"top": "0.5in", "bottom": "0.5in", "left": "0.5in", "right": "0.5in"})
    return path
