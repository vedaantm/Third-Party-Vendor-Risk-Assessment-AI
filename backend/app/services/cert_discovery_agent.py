# backend/app/services/cert_discovery_agent.py

import json
import asyncio
import requests
import urllib.parse
from bs4 import BeautifulSoup
from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from app.config_watsonx import get_llm

HEADERS = {"User-Agent": (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)}

MAX_TOOL_ROUNDS = 6
MAX_RETRIES_PER_CALL = 3


@tool
def search_web(query: str) -> str:
    """Search the web and return top results (title, url, snippet)."""
    try:
        resp = requests.post(
            f"https://html.duckduckgo.com/html/?q={query.replace(' ', '+')}",
            headers=HEADERS, timeout=10
        )
        soup = BeautifulSoup(resp.text, "html.parser")
        results = []
        for result in soup.find_all("div", class_="result")[:8]:
            a = result.find("a", class_="result__a")
            snippet_tag = result.find("a", class_="result__snippet")
            if not a:
                continue
            href = a.get("href", "")
            if "uddg=" in href:
                params = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
                real = params.get("uddg", [""])[0]
                if real:
                    href = urllib.parse.unquote(real)
            results.append({
                "title": a.get_text(strip=True),
                "url": href,
                "snippet": snippet_tag.get_text(strip=True) if snippet_tag else ""
            })
        return json.dumps(results) if results else "No results found."
    except Exception as e:
        return f"Search failed: {e}"


@tool
def fetch_page(url: str) -> str:
    """Fetch a webpage and return its visible text content."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer"]):
            tag.decompose()
        text = " ".join(soup.get_text(separator=" ").split())
        return text[:6000] if text else "Page returned no readable content."
    except Exception as e:
        return f"Fetch failed: {e}"


@tool
def submit_findings(findings_json: str) -> str:
    """Call this ONLY when confident you have enough evidence. Ends the investigation.

    findings_json must be a single JSON string (escaped, on one line) with
    EXACTLY this structure:
    {"certifications_found": ["ISO27001"], "evidence": ["..."], "uncertain": [], "explicitly_not_mentioned": ["GDPR"]}
    """
    return "Findings submitted."


TOOLS = [search_web, fetch_page, submit_findings]

SYSTEM_PROMPT = """You are a compliance investigator agent. Given a company
name, determine which industry-standard certifications and compliance
frameworks it actually holds (SOC1, SOC2_Type2, ISO27001, ISO27701, GDPR,
HIPAA, PCI_DSS, FedRAMP, CCPA, NIST_CSF, SOX, FCPA, CSA_STAR, and others
you discover).

You have three tools:
- search_web(query): find candidate pages.
- fetch_page(url): read a candidate page's actual text.
- submit_findings(findings_json): call once, when done. Pass a single
  JSON string with this structure (no markdown, no extra text):
  {"certifications_found": [], "evidence": [], "uncertain": [], "explicitly_not_mentioned": []}

Companies phrase certifications inconsistently — e.g. "SOC Type I and Type II"
means SOC1 AND SOC2_Type2. "ISO/IEC 27001:2022" means ISO27001. Use judgment,
not exact string matching.

Process: search first, read 2-4 promising pages, then submit_findings.
Do not submit_findings until you've fetched at least one real page."""


async def _invoke_with_retry(llm, messages):
    """Groq's tool-calling on Llama models occasionally emits malformed
    function-call syntax. This is intermittent — retrying the same
    request usually succeeds within a couple of attempts."""
    last_error = None
    for attempt in range(MAX_RETRIES_PER_CALL):
        try:
            return await llm.ainvoke(messages)
        except Exception as e:
            last_error = e
            if "tool_use_failed" in str(e) or "Failed to call a function" in str(e):
                await asyncio.sleep(1 + attempt)
                continue
            raise
    raise last_error


async def discover_certifications(company_name: str) -> dict:
    llm = get_llm(temperature=0.0).bind_tools(TOOLS)

    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=f"Investigate compliance certifications for: {company_name}")
    ]

    tool_map = {t.name: t for t in TOOLS}
    sources_used = []

    for _ in range(MAX_TOOL_ROUNDS):
        try:
            response = await _invoke_with_retry(llm, messages)
        except Exception as e:
            return {
                "certifications_found": [],
                "evidence": [],
                "uncertain": [f"Agent failed after retries: {e}"],
                "explicitly_not_mentioned": [],
                "sources_checked": sources_used,
            }

        messages.append(response)

        if not response.tool_calls:
            break

        for tool_call in response.tool_calls:
            name = tool_call["name"]
            args = tool_call["args"]

            if name == "submit_findings":
                try:
                    parsed = json.loads(args.get("findings_json", "{}"))
                except (json.JSONDecodeError, TypeError):
                    parsed = {}
                return {
                    "certifications_found": parsed.get("certifications_found", []),
                    "evidence": parsed.get("evidence", []),
                    "uncertain": parsed.get("uncertain", []),
                    "explicitly_not_mentioned": parsed.get("explicitly_not_mentioned", []),
                    "sources_checked": sources_used,
                }

            tool_fn = tool_map.get(name)
            output = tool_fn.invoke(args) if tool_fn else f"Unknown tool: {name}"
            if name == "fetch_page":
                sources_used.append(args.get("url", ""))

            messages.append(ToolMessage(content=str(output), tool_call_id=tool_call["id"]))

    return {
        "certifications_found": [],
        "evidence": [],
        "uncertain": [f"Agent did not complete investigation within {MAX_TOOL_ROUNDS} rounds"],
        "explicitly_not_mentioned": [],
        "sources_checked": sources_used,
    }