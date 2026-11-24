"""
Versioned LLM prompts.

Each prompt is a file `<name>.v<N>.md` in this directory. A change to a
prompt's wording is a new version file, not an edit, so results can be traced
to the exact prompt that produced them (the version is stored with them) and
the Phase 2 evals can compare versions. CLAUDE.md: changing a prompt needs
`make eval-smoke` and its metric diff in the PR.
"""

from dataclasses import dataclass
from functools import cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent


@dataclass(frozen=True)
class Prompt:
    name: str
    version: int
    text: str

    @property
    def id(self) -> str:
        """e.g. "soc2_extraction.v1", stored alongside what the prompt produced."""
        return f"{self.name}.v{self.version}"


@cache
def load_prompt(name: str, version: int) -> Prompt:
    path = PROMPTS_DIR / f"{name}.v{version}.md"
    if not path.is_file():
        raise FileNotFoundError(f"no prompt {name} v{version} at {path}")
    return Prompt(name, version, path.read_text(encoding="utf-8").strip())
