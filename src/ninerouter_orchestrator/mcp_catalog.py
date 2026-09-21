from __future__ import annotations

import re

from pydantic import Field

from .models import StrictModel


class McpCatalogItem(StrictModel):
    id: str = Field(pattern=r"^[a-z0-9-]+$")
    name: str
    rank: int = Field(ge=1, le=100)
    weekly_installs: int = Field(ge=0)


# Package names and endpoints change independently of popularity. The catalog deliberately stores
# discovery metadata only; users copy current connection details from each server's official source.
_ROWS = """
1|Playwright MCP|4631836
2|Firebase MCP|2163633
3|Browser Use|1689793
4|Storybook MCP Addon|1514346
5|Chrome DevTools MCP|1426792
6|Context7|1124903
7|Snyk|505048
8|AWS MCP Server|278552
9|Telnyx|217455
10|EdgarTools|194202
11|Scrapling MCP|169037
12|Desktop Commander|139749
13|Home Assistant MCP|132065
14|Azure MCP Server|120891
15|Hostinger API MCP|118838
16|Supabase|112086
17|agent-device|105363
18|GitLab MCP|96751
19|LLM Sandbox|93684
20|HOL Guard|90145
21|Sentry MCP|80346
22|UI5 MCP|71642
23|MongoDB MCP|71399
24|Nx MCP|71188
25|Next DevTools MCP|70848
26|SAP Fiori MCP|69035
27|ClickHouse MCP|64610
28|XcodeBuildMCP|62623
29|ComfyUI MCP|59538
30|Hevy MCP|58300
31|Figma Context MCP|56751
32|Argent|49759
33|SAP CAP MCP|43027
34|Sling CLI|37655
35|Appwrite MCP|33496
36|Perplexity MCP|29317
37|Ouroboros|29070
38|Serena MCP|28042
39|Google Workspace MCP|27360
40|Firecrawl MCP|27241
41|PostgreSQL MCP (YawLabs)|25294
42|Tencent CloudBase MCP|24539
43|Svelte MCP|24012
44|jCodemunch MCP|22794
45|Mobile MCP|20684
46|YunoHost MCP|19722
47|Microsoft 365 MCP|19322
48|PagerDuty MCP|18944
49|Skylos|18479
50|Claude Flow|18449
51|UniFi Network MCP|18444
52|Memorix|18338
53|Bernstein|17626
54|ClickUp|16927
55|Mbox MCP|16337
56|DBHub|15901
57|Tavily MCP|15543
58|MCP TS Core|15050
59|Dynatrace MCP|13958
60|BrowserStack|13890
61|Verdict QA|13684
62|Postman MCP Server|12516
63|PraisonAI|11807
64|WebCrypt MCP|11776
65|Sinter|11481
66|State Memory MCP|11184
67|Visual Memory MCP|11183
68|CrowdStrike Falcon MCP|10994
69|Brave Search MCP|10561
70|Codebase Memory|10416
71|Snowflake MCP|9809
72|RTFM|9734
73|Adscapi|9730
74|SmartBear MCP|9641
75|Aether Wealth|9618
76|Immich Photo Manager|8817
77|SearXNG Search|8543
78|arXiv MCP Server|8482
79|Ctxlint|8405
80|Zoo MCP|8364
81|Finance Toolkit|8277
82|Bourdon|8205
83|Deep Agentic Core MCP|8203
84|Obsidian MCP Server|8144
85|Unleash MCP|8079
86|SSH Policy-Gated Access|7828
87|Exomem|7753
88|HeyLead|7717
89|Redis MCP Server|7658
90|Ignite UI Theming MCP|7649
91|Neo4j Cypher MCP|7583
92|humanizer-ru|7569
93|InnoDay|7518
94|Google Drive MCP|7478
95|SAP MDK MCP|7389
96|Basic Memory|7255
97|Dynoxide|7132
98|ToolUniverse|6973
99|DBX|6886
100|Appium MCP|6572
"""


def _slug(name: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", name.lower())).strip("-")


def mcp_catalog() -> list[McpCatalogItem]:
    return [
        McpCatalogItem(
            id=_slug(name), name=name, rank=int(rank), weekly_installs=int(installs)
        )
        for row in _ROWS.strip().splitlines()
        for rank, name, installs in [row.split("|")]
    ]


def catalog_item(item_id: str) -> McpCatalogItem | None:
    return next((item for item in mcp_catalog() if item.id == item_id), None)
