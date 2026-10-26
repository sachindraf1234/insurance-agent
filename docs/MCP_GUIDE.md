# MCP guide — drive the system from an AI assistant

Our own MCP server (`mcp_server/server.py`, official `mcp` Python SDK / FastMCP) exposes:
`extract_documents` · `match_city` · `search_cities` · `calculate_dates` ·
`generate_insurer_excel` · `list_applicants` · `record_embassy_appointment` · `check_inbox_now`

## Claude Desktop
Add to `claude_desktop_config.json`, restart Claude:
```json
{"mcpServers": {"insurance-agent": {
  "command": "python",
  "args": ["C:/path/to/insurance-ai-agent/mcp_server/server.py"]}}}
```
Then ask: *"Extract these two files, calculate LOLC NEW dates for today, and draft the Excel"*.
**By design there is no send-email tool** — a person reviews in the dashboard and clicks Send.

## Useful free/open-source MCP servers & repos used or compatible
- microsoft/playwright-mcp — general browser automation MCP (our Playwright code is standalone too)
- modelcontextprotocol/servers — filesystem, fetch, sqlite reference servers
- PaddlePaddle/PaddleOCR · RapidAI/RapidOCR · UB-Mannheim/tesseract · ollama/ollama + qwen2.5-vl
- pymupdf/PyMuPDF · openpyxl · RapidFuzz/RapidFuzz · pallets/flask — core libraries
All free for commercial use (Apache/MIT/BSD). No OpenAI/cloud OCR dependency anywhere.
