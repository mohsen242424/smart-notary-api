import os
from jinja2 import Environment, FileSystemLoader, select_autoescape
from weasyprint import HTML

# الحصول على مسار مجلد utils الحالي
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))

# تحديد المسار الأساسي للمشروع (الرجوع خطوة للخلف من utils للوصول للجذر)
# هذا يضمن أن البرنامج يرى مجلدات templates و font الموجودة في جذر المشروع
BASE_DIR = os.path.dirname(CURRENT_DIR)


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
      - font/ 
          - Amiri-Regular.ttf

    Notes:
      - Arabic + RTL are handled by WeasyPrint (with proper font in template).
      - output_path should be an absolute temp path from caller.
    """
    # تحديد مسار مجلد القوالب بناءً على المسار الأساسي الجديد
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

    # base_url هنا أصبح يشير إلى جذر المشروع BASE_DIR لضمان العثور على الخطوط والصور
    try:
        HTML(string=html_content, base_url=BASE_DIR).write_pdf(output_path)
    except Exception as e:
        # هذا الخطأ يظهر عادةً في Render إذا كانت مكاتب النظام (Pango/Cairo) ناقصة
        raise RuntimeError(f"PDF generation failed: {e}")

    if not os.path.exists(output_path):
        raise RuntimeError("PDF file was not created.")

    return output_path
