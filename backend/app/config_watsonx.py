# Temporary: the module moved to app/llm/config.py. This keeps the protected
# importers (base_agent.py, cert_extraction_agent.py) working until their
# import lines are updated; delete it then.
from app.llm.config import PRIMARY_MODEL, get_llm  # noqa: F401
