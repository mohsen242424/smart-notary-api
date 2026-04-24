import os
from jinja2 import Environment, FileSystemLoader, select_autoescape
from weasyprint import HTML

# Root path of project (safe regardless of working dir)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def generate_pdf(template_name: str, data: dict, output_path: str) -> str:
    """
    Generate PDF from Jinja2 HTML template.

    Expected structure:
      - templates/
          - base.html
          - complaint.html
          - lawsuit_civil.html
          - lawsuit_renewal.html
          - poa_special.html
          - poa_irrevocable.html
      - fonts/
          - Amiri-Regular.ttf (optional but recommended)

    Notes:
      - Arabic + RTL are handled by WeasyPrint (with proper font in template).
      - output_path should be an absolute temp path from caller.
    """
    template_dir = os.path.join(BASE_DIR, "templates")
    if not os.path.isdir(template_dir):
        raise RuntimeError(f"Template directory not found: {template_dir}")

    env = Environment(
        loader=FileSystemLoader(template_dir),
        autoescape=select_autoescape(["html", "xml"]),
    )

    try:
        template = env.get_template(template_name)
    except Exception as e:
        raise RuntimeError(f"Template '{template_name}' not found or invalid: {e}")

    html_content = template.render(**data)

    # base_url is critical for local assets resolution (fonts/images)
    try:
        HTML(string=html_content, base_url=BASE_DIR).write_pdf(output_path)
    except Exception as e:
        raise RuntimeError(f"PDF generation failed: {e}")

    if not os.path.exists(output_path):
        raise RuntimeError("PDF file was not created.")

    return output_path
