from pathlib import Path
import os
from jinja2 import Environment, FileSystemLoader


def get_project_root():
    """Returns project root folder."""
    return Path(__file__).parent.parent


def file_exists(fn):
    try:
        with open(fn, 'r'):
            return True
    except IOError:
        return False


def get_environment_variable(var_name):
    if var_name:
        return os.environ.get(var_name.strip())
    return None


def populate_email_template(template_name: str, template_feeder: dict, templates_dir: Path) -> str:
    """Render a Jinja2 template from templates_dir with template_feeder exposed as 'process'."""
    file_loader = FileSystemLoader(str(templates_dir))
    env = Environment(loader=file_loader)
    env.trim_blocks = True
    env.lstrip_blocks = True
    env.rstrip_blocks = True
    template = env.get_template(template_name)
    return template.render(process=template_feeder)


def clean_email_body(email_body: str) -> str:
    """Strip newlines from email HTML to prevent Outlook rendering issues."""
    return email_body.replace('\r', '').replace('\n', '')
