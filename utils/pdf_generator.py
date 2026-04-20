import os
from jinja2 import Environment, FileSystemLoader
from weasyprint import HTML

# المسار الجذري للمشروع — ثابت بغض النظر عن مجلد التشغيل
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def generate_pdf(template_name: str, data: dict, output_path: str) -> str:
    """
    يُولّد ملف PDF من قالب Jinja2 HTML.

    ملاحظة: WeasyPrint يدعم العربية والـ RTL نيتيفلي عبر HarfBuzz،
    لذا لا حاجة لـ arabic_reshaper أو python-bidi هنا.
    الـ dir="rtl" في base.html كافٍ تماماً.
    """
    template_dir = os.path.join(BASE_DIR, "templates")
    env = Environment(loader=FileSystemLoader(template_dir))
    template = env.get_template(template_name)

    html_content = template.render(**data)

    # base_url مطلق لضمان تحميل الخطوط بشكل صحيح
    HTML(string=html_content, base_url=BASE_DIR).write_pdf(output_path)
    return output_path
