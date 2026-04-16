from fastapi.templating import Jinja2Templates
from app.version import VERSION

templates = Jinja2Templates(directory="app/templates")
templates.env.globals["version"] = VERSION
