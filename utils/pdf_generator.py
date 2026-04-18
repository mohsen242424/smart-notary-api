import arabic_reshaper
from bidi.algorithm import get_display
from jinja2 import Environment, FileSystemLoader
from weasyprint import HTML
import os

def generate_pdf(template_name, data, output_path):
    # إعداد Jinja2 لقراءة القوالب من مجلد templates
    template_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'templates')
    env = Environment(loader=FileSystemLoader(template_dir))
    template = env.get_template(template_name)

    # معالجة النصوص العربية (السر هنا!)
    processed_data = {}
    for key, value in data.items():
        if isinstance(value, str):
            reshaped_text = arabic_reshaper.reshape(value)
            processed_data[key] = get_display(reshaped_text)
        else:
            processed_data[key] = value

    # دمج البيانات
    html_content = template.render(processed_data)
    
    # تحويل لـ PDF
    # base_url="." عشان يقدر يوصل لمجلد الخطوط fonts
    HTML(string=html_content, base_url=".").write_pdf(output_path)
    return output_path
